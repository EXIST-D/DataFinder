"""官方网站栏目适配器：把"机构 → 栏目 → 条目（公报、数据发布、文件）"抓进统一目录。

适用于统计局、部委等发布机构的网站：不针对具体站点写代码，站点差异全部在 YAML 配置里。
- 机构（publisher）：名称、地区、首页；可选 `discover_nav`，从首页导航自动发现栏目；
- 栏目（column）：栏目页地址、条目类型、可选链接/标题正则与翻页上限；
- 抓取：栏目页若能解析出足够多的"条目链接"就逐页收录（公报、报告、文件）；解析不出的栏目只作为入口收录（link）。
抓取串行限速；站点拒绝/限流/连不上就放弃该机构并如实标注，不重试、不绕过。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..engine.errors import SourceError
from ..engine.http import Http
from .adapters import (
    CAP_DISCOVER,
    Adapter,
    AdapterError,
    DiscoverOptions,
    DiscoverResult,
    SourceInfo,
)
from .entities import Distribution, Entry, RegionLink, TopicLink, make_id
from .files import decode_html, distribution_for, fmt_of
from .listing import (
    Link,
    extract_links,
    infer_period,
    item_links,
    looks_like_article,
    page_url,
    redirect_target,
    site_of,
    total_pages,
)
from .repository import Catalog
from .rules import Target

MIN_ITEMS = 3  # 栏目页至少解析出这么多条目链接，才认为是列表页
DEFAULT_PAGES = 3  # 翻页上限默认值（栏目配置可覆盖）
MAX_NAV = 12  # 每个机构最多从首页导航里发现多少个栏目
TIMEOUT = 12.0
INTERVAL = 1.0  # 同一站点相邻请求的最小间隔（秒）


@dataclass(frozen=True)
class Column:
    id: str
    title: str
    url: str
    kind: str = "report"  # 条目的资源类型
    frequency: str | None = None
    item_title: str = "{text}"  # 条目标题模板：{text} 是链接文字，{column} 是栏目名
    href: str | None = None  # 条目链接正则（给了就不用默认启发式）
    text: str | None = None  # 条目标题正则
    pages: int | None = None
    topics: tuple[str, ...] = ()


@dataclass(frozen=True)
class NavRule:
    match: re.Pattern[str]
    kind: str
    frequency: str | None = None


@dataclass(frozen=True)
class Publisher:
    id: str
    name: str
    region: str
    home: str
    type: str = "statistics_bureau"
    columns: tuple[Column, ...] = ()
    discover_nav: bool = False
    interval: float | None = None  # 对该站点的最小请求间隔（秒）；防护敏感的站点调大


@dataclass
class SiteSpec:
    publishers: list[Publisher]
    nav: list[NavRule] = field(default_factory=list)
    nav_skip: re.Pattern[str] | None = None
    pages: int = DEFAULT_PAGES


def load_spec(data: dict[str, Any]) -> SiteSpec:
    """YAML 配置 → SiteSpec；配置写错在这里就报错，不留到抓取时。"""
    pubs, seen = [], set()
    for p in data["publishers"]:
        cols = tuple(
            Column(
                id=c["id"],
                title=c["title"],
                url=c["url"],
                kind=c.get("kind", "report"),
                frequency=c.get("frequency"),
                item_title=c.get("item_title", "{text}"),
                href=c.get("href"),
                text=c.get("text"),
                pages=c.get("pages"),
                topics=tuple(c.get("topics", ())),
            )
            for c in p.get("columns", ())
        )
        if p["id"] in seen:
            raise ValueError(f"机构 id 重复：{p['id']}")
        seen.add(p["id"])
        pubs.append(
            Publisher(
                p["id"],
                p["name"],
                p["region"],
                p["home"],
                p.get("type", "statistics_bureau"),
                cols,
                p.get("discover_nav", False),
                p.get("interval"),
            )
        )
    nav = [
        NavRule(re.compile(r["match"]), r["kind"], r.get("frequency")) for r in data.get("nav", ())
    ]
    skip = re.compile(data["nav_skip"]) if data.get("nav_skip") else None
    return SiteSpec(pubs, nav, skip, data.get("pages", DEFAULT_PAGES))


def source_id(pub: Publisher | str) -> str:
    return f"site.{pub if isinstance(pub, str) else pub.id}"


def _key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path}" + (f"?{parts.query}" if parts.query else "")


class SiteAdapter(Adapter):
    """通用实现；领域包继承并覆盖 `classify` 接入自己的主题/地域规则。"""

    id = "site"
    capabilities = frozenset({CAP_DISCOVER})

    def __init__(self, spec: SiteSpec, domain_id: str) -> None:
        self.spec = spec
        self.domain_id = domain_id

    # ── 领域扩展点 ──
    def classify(
        self, target: Target, base: list[RegionLink]
    ) -> tuple[list[TopicLink], list[RegionLink]]:
        return [], base

    # ── 来源与入口 ──
    def sources(self) -> list[SourceInfo]:
        return [
            SourceInfo(source_id(p), f"{p.name}网站", p.name, p.home) for p in self.spec.publishers
        ]

    def _base_region(self, pub: Publisher) -> list[RegionLink]:
        return [RegionLink(pub.region, "covers", None, "exact", "structure")]

    def _entry(
        self, pub: Publisher, kind: str, key: str, title: str, target: Target, **kw: Any
    ) -> Entry:
        e = Entry(source_id(pub), kind, key, title, **kw)
        e.topics, e.regions = self.classify(target, self._base_region(pub))
        return e

    def home_entry(
        self,
        pub: Publisher,
        *,
        access_status: str = "unknown",
        note: str | None = None,
        verified: bool = False,
    ) -> Entry:
        manual = access_status in ("blocked", "unavailable")
        return self._entry(
            pub,
            "portal",
            "home",
            f"{pub.name}网站",
            Target(),
            landing_url=pub.home,
            access_status=access_status,
            verification_level="observed" if verified else "source_asserted",
            distributions=[
                Distribution(
                    "manual" if manual else "page",
                    pub.home,
                    note=note or ("站点拒绝自动访问或连不上，请用浏览器打开" if manual else None),
                )
            ],
            extra={"publisher_type": pub.type},
        )

    @staticmethod
    def _with_topics(e: Entry, col: Column) -> Entry:
        have = {x.topic_id for x in e.topics}
        e.topics += [TopicLink(t, "manual") for t in col.topics if t not in have]
        return e

    def column_entry(
        self,
        pub: Publisher,
        col: Column,
        *,
        access_status: str = "unknown",
        verified: bool = False,
        note: str | None = None,
    ) -> Entry:
        manual = access_status in ("blocked", "unavailable")
        e = self._entry(
            pub,
            "portal",
            f"col/{col.id}",
            f"{pub.name}·{col.title}",
            Target(top=col.title, path=[col.title], title=col.title),
            parent_id=make_id(source_id(pub), "portal", "home"),
            path=f"{pub.name} › {col.title}",
            landing_url=col.url,
            access_status=access_status,
            verification_level="observed" if verified else "source_asserted",
            distributions=[
                Distribution(
                    "manual" if manual else "page",
                    col.url,
                    note=note or ("站点拒绝自动访问，请用浏览器打开" if manual else None),
                )
            ],
            extra={"column": col.title, "item_kind": col.kind},
        )
        return self._with_topics(e, col)

    def item_entry(self, pub: Publisher, col: Column, link: Link) -> Entry:
        title = col.item_title.format(text=link.text, column=col.title)
        year, freq, published = infer_period(title, link.date)
        fmt = fmt_of(link.href)
        dist = distribution_for(link.href) if fmt != "html" else Distribution("page", link.href)
        e = self._entry(
            pub,
            col.kind,
            _key(link.href),
            title,
            Target(top=title, path=[col.title, title], title=title),
            parent_id=make_id(source_id(pub), "portal", f"col/{col.id}"),
            path=f"{pub.name} › {col.title}",
            landing_url=link.href,
            edition_year=published,
            period_start=year,
            period_end=year,
            frequency=col.frequency or freq,
            verification_level="source_asserted",
            distributions=[dist],
            extra={"date": link.date, "column": col.id},
        )
        return self._with_topics(e, col)

    # ── 抓取 ──
    async def discover(self, cat: Catalog, opts: DiscoverOptions) -> DiscoverResult:
        wanted = {x.strip() for x in opts.params.get("site", "").split(",") if x.strip()}
        known = {p.id for p in self.spec.publishers}
        if wanted - known:
            raise AdapterError(
                f"未知机构：{', '.join(sorted(wanted - known))}（可选：{', '.join(sorted(known))}）"
            )
        pubs = [p for p in self.spec.publishers if not wanted or p.id in wanted]
        self.register_sources(cat)
        # 机构首页与已配置的栏目入口先登记（不联网；已有的不覆盖，保留抓取得到的状态）
        for p in pubs:
            self._register(cat, [self.home_entry(p), *(self.column_entry(p, c) for c in p.columns)])
        run = cat.start_run("site", "discover")
        result = DiscoverResult("done")
        slow = {urlsplit(p.home).netloc: p.interval for p in pubs if p.interval}
        http = Http(min_interval=slow, default_interval=INTERVAL)
        budget: list[int | None] = [opts.max_requests]
        try:
            for p in pubs:
                if budget[0] is not None and budget[0] <= 0:
                    result.status = "limited"
                    break
                await self._publisher(cat, http, p, opts, result, budget)
        finally:
            await http.aclose()
        cat.finish_run(
            run,
            "ok",
            seen=result.seen,
            changed=result.added,
            message="；".join(result.stopped) or None,
        )
        return result

    @staticmethod
    def _register(cat: Catalog, entries: list[Entry]) -> None:
        cat.upsert([e for e in entries if not cat.exists(e.resource_id)])

    async def _get(
        self, http: Http, url: str, result: DiscoverResult, budget: list[int | None]
    ) -> str | None:
        if budget[0] is not None:
            budget[0] -= 1
        result.requests += 1
        code, content = await http.get_bytes(url, timeout=TIMEOUT)
        return None if code in (404, 410) else decode_html(content)

    async def _publisher(
        self,
        cat: Catalog,
        http: Http,
        pub: Publisher,
        opts: DiscoverOptions,
        result: DiscoverResult,
        budget: list[int | None],
    ) -> None:
        columns = list(pub.columns)
        before = result.requests
        try:
            if pub.discover_nav:
                home = await self._get(http, pub.home, result, budget)
                found = self._nav_columns(pub, home or "", columns)
                self._drop_stale_nav(cat, pub, {c.id for c in found})
                self._register(cat, [self.column_entry(pub, c) for c in found])
                opts.say(f"{pub.name}：从首页发现 {len(found)} 个栏目")
                columns += found
            for col in columns:
                await self._column(cat, http, pub, col, opts, result, budget)
            if (
                result.requests > before
            ):  # 这一轮确实访问成功过：把早先因防护中断留下的"拒绝/连不上"状态改回来
                cat.upsert([self.home_entry(pub, access_status="supported", verified=True)])
        except SourceError as exc:
            result.stopped.append(f"{pub.name}：{exc}")
            opts.say(f"{pub.name}：{exc}（放弃该机构，不重试不绕过）")
            blocked = exc.kind in ("blocked", "rate_limited")
            cat.upsert(
                [
                    self.home_entry(
                        pub, access_status="blocked" if blocked else "unavailable", note=str(exc)
                    )
                ]
            )

    @staticmethod
    def _drop_stale_nav(cat: Catalog, pub: Publisher, keep: set[str]) -> None:
        """首页导航变了：上次发现、这次没有的栏目连同其条目一起删掉。"""
        sid = source_id(pub)
        for row in cat.conn.execute(
            "SELECT resource_id, source_key FROM resources WHERE source_id=? AND kind='portal'"
            " AND source_key LIKE 'col/nav-%'",
            (sid,),
        ).fetchall():
            if row["source_key"].removeprefix("col/") not in keep:
                cat.delete(row["resource_id"])

    def _nav_columns(self, pub: Publisher, html: str, known: list[Column]) -> list[Column]:
        """从首页导航里发现栏目：链接文字命中导航规则、同站、不是文件、不与已配置栏目重复。"""
        have = {c.url for c in known} | {pub.home}
        out: list[Column] = []
        for link in extract_links(html, pub.home) if html else []:
            if not 2 <= len(link.text) <= 12 or site_of(link.href) != site_of(pub.home):
                continue
            if link.href in have or looks_like_article(link.href):
                continue
            if fmt_of(link.href) in ("pdf", "xls", "xlsx", "doc", "docx"):
                continue
            if self.spec.nav_skip and self.spec.nav_skip.search(link.text):
                continue
            rule = next((r for r in self.spec.nav if r.match.search(link.text)), None)
            if rule is None:
                continue
            have.add(link.href)
            cid = "nav-" + hashlib.sha1(link.href.encode()).hexdigest()[:8]
            out.append(Column(cid, link.text, link.href, rule.kind, rule.frequency))
            if len(out) >= MAX_NAV:
                break
        return out

    async def _column(
        self,
        cat: Catalog,
        http: Http,
        pub: Publisher,
        col: Column,
        opts: DiscoverOptions,
        result: DiscoverResult,
        budget: list[int | None],
    ) -> None:
        rid = make_id(source_id(pub), "portal", f"col/{col.id}")
        if budget[0] is not None and budget[0] <= 0:
            result.status = "limited"
            return
        if not opts.refresh and cat.access_status(rid) == "supported":
            return  # 已成功抓过；失败/未抓过的栏目续跑时会重试
        try:
            html = await self._get(http, col.url, result, budget)
        except SourceError as exc:
            if exc.kind in ("blocked", "rate_limited"):
                raise
            cat.upsert([self.column_entry(pub, col, access_status="unavailable", note=str(exc))])
            result.stopped.append(f"{pub.name}·{col.title}：{exc}")
            opts.say(f"{pub.name}·{col.title}：{exc}")
            return
        if html is None:
            cat.upsert(
                [
                    self.column_entry(
                        pub, col, access_status="unavailable", note="栏目页不存在（404）"
                    )
                ]
            )
            opts.say(f"{pub.name}·{col.title}：页面不存在")
            return
        base = col.url
        if jump := redirect_target(html, base):  # 栏目页只是个跳转：跟一跳
            if (nxt := await self._get(http, jump, result, budget)) is not None:
                html, base = nxt, jump
        links = extract_links(html, base)
        raw = item_links(
            links, base, href=col.href
        )  # 标题过滤之前：用来判断"这是不是列表页"和翻页是否到头
        items = item_links(links, base, href=col.href, text=col.text)
        if len(raw) < MIN_ITEMS:
            cat.upsert([self.column_entry(pub, col, access_status="supported", verified=True)])
            opts.say(f"{pub.name}·{col.title}：栏目入口（未解析出列表）")
            return
        cat.delete_children(rid)
        limit = min(col.pages or self.spec.pages, total_pages(html))
        seen = {link.href for link in items}
        seen_raw = {link.href for link in raw}
        all_items = list(items)
        for n in range(2, limit + 1):
            if budget[0] is not None and budget[0] <= 0:
                result.status = "limited"
                break
            nxt = await self._get(http, page_url(base, n, html), result, budget)
            if nxt is None:
                break
            page_links = extract_links(nxt, base)
            page_raw = [
                x for x in item_links(page_links, base, href=col.href) if x.href not in seen_raw
            ]
            if (
                not page_raw
            ):  # 这一页没有新条目：翻到头了（标题过滤后为空不算到头，后面几页可能还有）
                break
            seen_raw |= {x.href for x in page_raw}
            fresh = [
                x
                for x in item_links(page_links, base, href=col.href, text=col.text)
                if x.href not in seen
            ]
            seen |= {x.href for x in fresh}
            all_items += fresh
        entries = [self.item_entry(pub, col, x) for x in all_items]
        cat.upsert(
            [self.column_entry(pub, col, access_status="supported", verified=True), *entries]
        )
        result.seen += len(entries)
        result.added += len(entries)
        opts.say(f"{pub.name}·{col.title}：{len(entries)} 条")
