"""逐版次抓取年鉴目录：串行、限速、可续跑；404 记为"该版次不存在"，其他错误如实记录。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from datafinder.engine.errors import SourceError
from datafinder.engine.http import Http

from .index import replace_edition
from .parse import decode, parse_toc
from .registry import Series

MIN_ENTRIES = 20  # 少于这个数当作"不是目录页"，避免把错误页当成目录
HEADERS = {"Accept": "text/html,*/*"}


@dataclass
class CrawlResult:
    ok: list[tuple[str, int, int]] = field(default_factory=list)  # (series, year, tables)
    missing: list[tuple[str, int]] = field(default_factory=list)
    failed: list[tuple[str, int, str]] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)  # 因站点防护/拒绝而放弃的系列及原因


async def crawl_edition(http: Http, series: Series, year: int) -> tuple[str, str | None, list | None, str]:
    """返回 (status, toc_url, entries, message)；status: ok | missing | error。"""
    last_msg = ""
    for url in series.toc_urls(year):
        code, content = await http.get_bytes(url, headers=HEADERS)
        if code in (404, 410):
            continue
        entries = parse_toc(decode(content), url)
        n = sum(1 for e in entries if e.kind == "table")
        if n >= MIN_ENTRIES:
            return "ok", url, entries, ""
        last_msg = f"{url} 解析出的数据表只有 {n} 张，不像年鉴目录页"
    return ("error", None, None, last_msg) if last_msg else ("missing", None, None, "该版次的目录页不存在（404）")


async def build(
    http: Http,
    conn: sqlite3.Connection,
    series_list: list[Series],
    *,
    years: tuple[int | None, int | None] = (None, None),
    refresh: bool = False,
    progress: Callable[[str], None] | None = None,
) -> CrawlResult:
    say = progress or (lambda _m: None)
    res = CrawlResult()
    done = {
        (r["series"], r["year"])
        for r in conn.execute("SELECT series, year FROM edition WHERE status IN ('ok','missing')")
    }
    for s in series_list:
        if not s.crawl:
            continue
        for year in s.edition_years(*years):
            if not refresh and (s.id, year) in done:
                continue
            try:
                status, toc, entries, msg = await crawl_edition(http, s, year)
            except SourceError as exc:
                replace_edition(conn, s.id, year, status="error", toc_url=None, message=str(exc))
                res.failed.append((s.id, year, str(exc)))
                say(f"{s.zh} {year}：失败（{exc}）")
                if exc.kind in ("rate_limited", "blocked", "down", "timeout"):
                    # 站点拒绝/限流/连不上：这一系列到此为止（不重试、不绕过），继续下一个系列
                    res.stopped.append(f"{s.zh}：{exc}")
                    break
                continue
            replace_edition(conn, s.id, year, status=status, toc_url=toc, entries=entries, message=msg)
            if status == "ok":
                n = sum(1 for e in entries or [] if e.kind == "table")
                res.ok.append((s.id, year, n))
                say(f"{s.zh} {year}：{n} 张表")
            elif status == "missing":
                res.missing.append((s.id, year))
                say(f"{s.zh} {year}：无此版次")
            else:
                res.failed.append((s.id, year, msg))
                say(f"{s.zh} {year}：{msg}")
    return res
