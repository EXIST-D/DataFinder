"""统计年鉴目录：覆盖情况、按关键词找数据表（含跨年份版次）。"""

from __future__ import annotations

from typing import Any

from ..domains.official_statistics_cn.yearbook import index as yb_index
from .errors import ServiceError

NOT_BUILT = (
    "年鉴目录还没建立。请先在终端运行 `datafinder yearbook index`（几分钟，串行限速，可中断续跑）。"
)


def yearbook_catalog() -> dict[str, Any]:
    """各年鉴系列的覆盖情况：已建目录的年份、缺失版次、是否可抓取，以及给人看的入口。"""
    conn = yb_index.connect_index()
    try:
        return {"series": yb_index.coverage(conn), "summary": yb_index.stats(conn)}
    finally:
        conn.close()


def yearbook_find(
    query: str,
    *,
    region: str | None = None,
    year: int | None = None,
    series: str | None = None,
    fmt: str | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    if not query.strip():
        raise ServiceError("query 不能为空")
    conn = yb_index.connect_index()
    try:
        info = yb_index.stats(conn)
        if not info["tables"]:
            raise ServiceError(NOT_BUILT)
        hits = yb_index.search(
            conn, query, region=region, year=year, series=series, fmt=fmt, limit=max(1, min(limit, 50))
        )
    finally:
        conn.close()
    return {
        "query": query,
        "tables": hits,
        "indexed": {k: {"name": v["name"], "years": v["years"]} for k, v in info["series"].items()},
        "note": "表格文件的格式以目录页给出的为准（较新的版次多为图片，较早的为网页表格）；"
        "要可直接分析的数值请优先用 nbs_find / nbs_fetch。",
        "hint": None if hits else "没有命中；换个说法，或去掉地区/年份限定再试。",
    }
