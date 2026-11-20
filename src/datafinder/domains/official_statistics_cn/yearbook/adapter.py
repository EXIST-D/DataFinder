"""统计年鉴适配器：目录抓取（discover）与旧索引迁移（migrate）。年鉴目录里的文件直链由通用下载能力处理。

每个版次一个请求，串行、间隔 ≥1 秒；404 的版次不收录；站点拒绝/限流/连不上时放弃该系列（不重试不绕过）并标注原因。
"""

from __future__ import annotations

from datafinder.catalog.adapters import (
    CAP_DISCOVER,
    CAP_MIGRATE,
    Adapter,
    AdapterError,
    DiscoverOptions,
    DiscoverResult,
    SourceInfo,
)
from datafinder.catalog.repository import Catalog
from datafinder.engine.errors import SourceError
from datafinder.engine.http import Http

from .. import DOMAIN_ID
from .entries import edition_entry, edition_resource_id, series_entry, source_id, table_entries
from .parse import decode, parse_toc
from .registry import Series, load_series

MIN_TABLES = 20  # 少于这个数当作"不是目录页"，避免把错误页当成目录
HEADERS = {"Accept": "text/html,*/*"}


def _hosts(series_list: list[Series]) -> dict[str, float]:
    from urllib.parse import urlsplit

    return {urlsplit(s.base.format(year=2024)).netloc: 1.0 for s in series_list if s.base}


async def fetch_edition(http: Http, series: Series, year: int):
    """返回 (status, toc_url, toc_entries, message)；status: ok | missing | error。"""
    last = ""
    for url in series.toc_urls(year):
        code, content = await http.get_bytes(url, headers=HEADERS)
        if code in (404, 410):
            continue
        toc = parse_toc(decode(content), url)
        n = sum(1 for e in toc if e.kind == "table")
        if n >= MIN_TABLES:
            return "ok", url, toc, ""
        last = f"{url} 解析出的数据表只有 {n} 张，不像年鉴目录页"
    return ("error", None, None, last) if last else ("missing", None, None, "该版次的目录页不存在（404）")


class YearbookAdapter(Adapter):
    id = "yearbook"
    domain_id = DOMAIN_ID
    capabilities = frozenset({CAP_DISCOVER, CAP_MIGRATE})

    def sources(self) -> list[SourceInfo]:
        return [SourceInfo(source_id(s), s.zh, s.publisher, s.home) for s in load_series()]

    async def discover(self, cat: Catalog, opts: DiscoverOptions) -> DiscoverResult:
        wanted = {x.strip() for x in opts.params.get("series", "").split(",") if x.strip()}
        known = {s.id for s in load_series()}
        if wanted - known:
            raise AdapterError(f"未知年鉴系列：{', '.join(sorted(wanted - known))}（可选：{', '.join(sorted(known))}）")
        lo = int(opts.params["from"]) if opts.params.get("from") else None
        hi = int(opts.params["to"]) if opts.params.get("to") else None
        self.register_sources(cat)
        # 所有系列的导航入口先登记（不联网）：抓不到的系列至少能给出官方入口
        cat.upsert(series_entry(s) for s in load_series() if not wanted or s.id in wanted)
        crawlable = [s for s in load_series() if s.crawl and (not wanted or s.id in wanted)]
        run = cat.start_run("yearbook", "discover")
        result = DiscoverResult("done")
        http = Http(min_interval=_hosts(crawlable))
        try:
            for s in crawlable:
                await self._series(cat, http, s, lo, hi, opts, result)
        finally:
            await http.aclose()
        cat.finish_run(run, "ok", seen=result.seen, changed=result.added, message="；".join(result.stopped) or None)
        return result

    async def _series(
        self, cat: Catalog, http: Http, s: Series, lo: int | None, hi: int | None, opts: DiscoverOptions,
        result: DiscoverResult,
    ) -> None:
        for year in s.edition_years(lo, hi):
            rid = edition_resource_id(s, year)
            if not opts.refresh and cat.exists(rid):
                continue
            try:
                status, toc_url, toc, msg = await fetch_edition(http, s, year)
            except SourceError as exc:
                blocked = exc.kind in ("blocked", "rate_limited")
                cat.upsert([series_entry(s, access_status="blocked" if blocked else "unavailable", note=str(exc))])
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

    def legacy_present(self) -> bool:
        from datafinder.config import data_dir

        from ..legacy import YEARBOOK_LEGACY

        return (data_dir() / YEARBOOK_LEGACY).exists()

    def migrate(self, cat: Catalog) -> dict:
        from ..legacy import migrate_yearbook

        return migrate_yearbook(cat)
