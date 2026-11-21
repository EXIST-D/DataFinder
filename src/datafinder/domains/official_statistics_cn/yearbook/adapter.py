"""统计年鉴适配器：目录抓取（discover）与旧索引迁移（migrate）。年鉴目录里的文件直链由通用下载能力处理。

每个版次一个请求，串行、间隔 ≥1 秒；404 的版次不收录；站点拒绝/限流/连不上时放弃该系列（不重试不绕过）并标注原因。
"""

from __future__ import annotations

import re

from datafinder.catalog import regions
from datafinder.catalog.adapters import (
    CAP_DISCOVER,
    CAP_FETCH,
    CAP_MIGRATE,
    Adapter,
    AdapterError,
    DiscoverOptions,
    DiscoverResult,
    FetchParams,
    SourceInfo,
)
from datafinder.catalog.entities import Distribution, Entry
from datafinder.catalog.files import decode_html, distribution_for, fmt_of
from datafinder.catalog.htmltable import frame_layout, has_table, layout_urls, to_table
from datafinder.catalog.listing import Link, extract_links
from datafinder.catalog.repository import Catalog
from datafinder.engine.errors import SourceError
from datafinder.engine.http import Http
from datafinder.tables import Table

from .. import DOMAIN_ID
from .entries import (
    edition_entry,
    edition_resource_id,
    listed_edition_entry,
    series_entry,
    source_id,
    table_entries,
    table_key,
)
from .parse import TocEntry, parse_toc, parse_tree_toc
from .registry import Series, load_series

PARSERS = {"zk": parse_toc, "tree": parse_tree_toc}
ARCHIVES = {"zip", "rar", "xls", "xlsx", "pdf", "doc", "docx"}  # 版次页上可下载的附件类型
_YEAR = re.compile(r"(?:19|20)\d{2}")
MIN_TABLES = 20  # 少于这个数当作"不是目录页"，避免把错误页当成目录
HEADERS = {"Accept": "text/html,*/*"}


def _hosts(series_list: list[Series]) -> dict[str, float]:
    from urllib.parse import urlsplit

    return {
        urlsplit(s.base_for(s.edition_years()[0])).netloc: 1.0
        for s in series_list
        if s.edition_years()
    }


def _merge_alternates(primary: list[TocEntry], others: list[list[TocEntry]]) -> list[TocEntry]:
    """同一版次常有多份目录（网页版、Excel 版）。以第一份为准，按"表号+出现次序"把其他格式并进来。"""
    from dataclasses import replace

    def keyed(toc: list[TocEntry]) -> dict[tuple[str, int], TocEntry]:
        seen: dict[str, int] = {}
        out = {}
        for t in toc:
            if t.kind == "table":
                k = table_key(t)
                seen[k] = seen.get(k, 0) + 1
                out[(k, seen[k])] = t
        return out

    base = keyed(primary)
    extra = []
    for toc in others:
        alt = keyed(toc)
        if len(set(alt) & set(base)) >= 0.8 * len(base):  # 对不上号的目录不并（避免张冠李戴）
            extra.append(alt)
    if not extra:
        return primary
    out, seen = [], {}
    for t in primary:
        if t.kind != "table":
            out.append(t)
            continue
        k = table_key(t)
        seen[k] = seen.get(k, 0) + 1
        more = tuple((a[(k, seen[k])].url, a[(k, seen[k])].fmt) for a in extra if (k, seen[k]) in a)
        out.append(replace(t, others=more))
    return out


async def fetch_edition(http: Http, series: Series, year: int):
    """返回 (status, toc_url, toc_entries, message)；status: ok | missing | error。"""
    last = ""
    good: list[tuple[str, list[TocEntry]]] = []
    for url in series.toc_urls(year):
        code, content = await http.get_bytes(url, headers=HEADERS)
        if code in (404, 410):
            continue
        toc = PARSERS[series.format](decode_html(content), url)
        n = sum(1 for e in toc if e.kind == "table")
        if n >= MIN_TABLES:
            good.append((url, toc))
        else:
            last = f"{url} 解析出的数据表只有 {n} 张，不像年鉴目录页"
    if good:
        return "ok", good[0][0], _merge_alternates(good[0][1], [t for _, t in good[1:]]), ""
    return (
        ("error", None, None, last)
        if last
        else ("missing", None, None, "该版次的目录页不存在（404）")
    )


async def _html_with_table(http: Http, url: str):
    """取网页；若是框架页（上海年鉴：标题、角标、表头、行标签、数据各在一个框架里），把各子框架取回来，
    按框架的嵌套关系（上下 rows / 左右 cols）组成布局。返回 (页面或布局, 实际地址)。"""
    code, content = await http.get_bytes(url, headers=HEADERS)
    if code in (404, 410):
        raise AdapterError(f"网页不存在（HTTP {code}）：{url}")
    html = decode_html(content)
    if has_table(html):
        return html, url
    tree = frame_layout(html, url)
    if tree is None:
        raise AdapterError("页面里没有表格（可能是图片版或纯文字页）：请用 inspect 查看其他格式")
    pages: dict[str, str] = {}
    for frame in layout_urls(tree)[:12]:
        code, content = await http.get_bytes(frame, headers=HEADERS)
        pages[frame] = decode_html(content) if code == 200 else ""

    def build(node):
        kind, kids = node
        return (
            kind,
            [build(k) if isinstance(k, tuple) else pages.get(k, "") for k in kids],
        )

    return build(tree), url


def _apply_filters(table: Table, params: FetchParams) -> Table:
    """regions：只留这些地区的行（["全部"] 不筛）；start/end：只留这段年份（没有期别的行保留）。"""
    rows = table.rows
    wanted = [r for r in params.regions if r and r not in ("全部", "all")]
    if wanted:

        def norm(x: str) -> str:
            return re.sub(r"(省|市|自治区|特别行政区)$", "", x.strip())

        want = {norm(w) for w in wanted}
        have = sorted({r[2] for r in rows})
        rows = [r for r in rows if norm(r[2]) in want]
        if not rows:
            raise AdapterError(
                f"表里没有这些地区的数据：{', '.join(wanted)}（表里有：{', '.join(have[:12])}）"
            )
    lo = int(params.start) if params.start and params.start[:4].isdigit() else None
    hi = int(params.end) if params.end and params.end[:4].isdigit() else None
    if lo or hi:

        def ok(r: list) -> bool:
            y = int(r[1][:4]) if r[1] else None
            return y is None or ((lo is None or y >= lo) and (hi is None or y <= hi))

        rows = [r for r in rows if ok(r)]
    table.rows = rows
    return table


class YearbookAdapter(Adapter):
    id = "yearbook"
    domain_id = DOMAIN_ID
    capabilities = frozenset({CAP_DISCOVER, CAP_FETCH, CAP_MIGRATE})

    def sources(self) -> list[SourceInfo]:
        return [SourceInfo(source_id(s), s.zh, s.publisher, s.home) for s in load_series()]

    async def discover(self, cat: Catalog, opts: DiscoverOptions) -> DiscoverResult:
        wanted = {x.strip() for x in opts.params.get("series", "").split(",") if x.strip()}
        known = {s.id for s in load_series()}
        if wanted - known:
            raise AdapterError(
                f"未知年鉴系列：{', '.join(sorted(wanted - known))}（可选：{', '.join(sorted(known))}）"
            )
        lo = int(opts.params["from"]) if opts.params.get("from") else None
        hi = int(opts.params["to"]) if opts.params.get("to") else None
        self.register_sources(cat)
        # 所有系列的导航入口先登记（不联网）：抓不到的系列至少能给出官方入口
        cat.upsert(series_entry(s) for s in load_series() if not wanted or s.id in wanted)
        crawlable = [s for s in load_series() if s.crawl and (not wanted or s.id in wanted)]
        run = cat.start_run("yearbook", "discover")
        result = DiscoverResult("done")
        http = Http(min_interval=_hosts(crawlable), default_interval=1.0)
        try:
            for s in crawlable:
                await self._series(cat, http, s, lo, hi, opts, result)
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

    async def _series(
        self,
        cat: Catalog,
        http: Http,
        s: Series,
        lo: int | None,
        hi: int | None,
        opts: DiscoverOptions,
        result: DiscoverResult,
    ) -> None:
        if s.format == "list":
            await self._list_series(cat, http, s, lo, hi, opts, result)
            return
        for year in s.edition_years(lo, hi):
            rid = edition_resource_id(s, year)
            if not opts.refresh and cat.exists(rid):
                continue
            try:
                status, toc_url, toc, msg = await fetch_edition(http, s, year)
            except SourceError as exc:
                blocked = exc.kind in ("blocked", "rate_limited")
                cat.upsert(
                    [
                        series_entry(
                            s, access_status="blocked" if blocked else "unavailable", note=str(exc)
                        )
                    ]
                )
                result.stopped.append(f"{s.zh}：{exc}")
                opts.say(f"{s.zh} {year}：{exc}（放弃该系列，不重试不绕过）")
                return
            result.requests += 1
            if status == "missing":
                opts.say(f"{s.zh} {year}：无此版次")
                continue
            if status == "error":
                opts.say(f"{s.zh} {year}：{msg}")
                continue
            cat.delete_children(rid)
            tables = table_entries(s, year, toc or [])
            cat.upsert([edition_entry(s, year, toc_url or ""), *tables])
            result.seen += 1 + len(tables)
            result.added += 1 + len(tables)
            opts.say(f"{s.zh} {year}：{len(tables)} 张表")

    async def _list_series(
        self,
        cat: Catalog,
        http: Http,
        s: Series,
        lo: int | None,
        hi: int | None,
        opts: DiscoverOptions,
        result: DiscoverResult,
    ) -> None:
        """只有"各版次列表"的年鉴（广东、湖北）：每个版次登记一个条目，附件（zip/xls/pdf）作为可下载入口。"""
        found: dict[int, Link] = {}
        try:
            for url in s.list_urls:
                result.requests += 1
                code, content = await http.get_bytes(url, headers=HEADERS)
                if code in (404, 410):
                    continue
                for link in extract_links(decode_html(content), url):
                    m = _YEAR.search(link.text)
                    if not m or "统计年鉴" not in link.text:
                        continue
                    year = int(m.group(0))
                    if (lo or 0) <= year <= (hi or 9999):
                        found.setdefault(year, link)
            for year, link in sorted(found.items(), reverse=True):
                rid = edition_resource_id(s, year)
                if not opts.refresh and cat.exists(rid):
                    continue
                files = [distribution_for(link.href)] if fmt_of(link.href) in ARCHIVES else []
                if s.follow and not files:
                    result.requests += 1
                    code, content = await http.get_bytes(link.href, headers=HEADERS)
                    if code not in (404, 410):
                        for x in extract_links(decode_html(content), link.href):
                            if fmt_of(x.href) in ARCHIVES:
                                files.append(distribution_for(x.href))
                            elif "在线浏览" in x.text:
                                files.append(Distribution("page", x.href, note="在线浏览"))
                cat.upsert([listed_edition_entry(s, year, link.href, files)])
                result.seen += 1
                result.added += 1
                opts.say(f"{s.zh} {year}：{len(files)} 个可下载/浏览入口")
        except SourceError as exc:
            cat.upsert(
                [
                    series_entry(
                        s,
                        access_status="blocked"
                        if exc.kind in ("blocked", "rate_limited")
                        else "unavailable",
                        note=str(exc),
                    )
                ]
            )
            result.stopped.append(f"{s.zh}：{exc}")
            opts.say(f"{s.zh}：{exc}（放弃该系列，不重试不绕过）")

    async def fetch(self, cat: Catalog, entry: Entry, params: FetchParams) -> Table:
        """网页表格（html_parse 入口）→ 整洁长表。表是图片/文件的资源不能取数（请用 download）。"""
        dist = next((d for d in entry.distributions if d.access_method == "html_parse"), None)
        if dist is None or not dist.url:
            raise AdapterError(
                "这张表没有可解析的网页版本（只有文件或图片）：可直接下载的用 download，图片需要 OCR"
            )
        url = dist.url.strip().replace("\\", "/")
        http = Http(default_interval=1.0)
        try:
            html, used = await _html_with_table(http, url)
        finally:
            await http.aclose()
        primary = next((r.region_id for r in entry.regions if r.relation == "covers"), "CN")
        census = entry.source_id.startswith("census.")
        table = to_table(
            html,
            default_region="全国" if primary == "CN" else regions.name_zh(primary),
            # 普查表的参考年就是版次年；年鉴的版次年是出版年、不是观测年，不能拿来当期别
            default_year=str(entry.edition_year) if census and entry.edition_year else None,
            source_url=used,
            title_hint=entry.title,
        )
        table.meta["资源"] = entry.resource_id
        return _apply_filters(table, params)

    def legacy_present(self) -> bool:
        from datafinder.config import data_dir

        from ..legacy import YEARBOOK_LEGACY

        return (data_dir() / YEARBOOK_LEGACY).exists()

    def migrate(self, cat: Catalog) -> dict:
        from ..legacy import migrate_yearbook

        return migrate_yearbook(cat)
