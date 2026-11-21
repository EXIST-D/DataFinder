"""目录树：把当前检索请求的命中集合按主题/地域/来源展开（只读、本地、毫秒级）。"""

from __future__ import annotations

from typing import Any

from datafinder.catalog import SearchRequest
from datafinder.catalog.tree import axes_overview, tree_children

from .common import open_catalog
from .errors import guard


def tree(
    axis: str | None = None,
    node: str | None = None,
    query: str = "",
    *,
    domain_ids: list[str] | None = None,
    topic_ids: list[str] | None = None,
    region_ids: list[str] | None = None,
    region_match: str = "intersects",
    geo_granularity: str | None = None,
    kinds_: list[str] | None = None,
    delivery: list[str] | None = None,
    period_start: int | None = None,
    period_end: int | None = None,
    frequency: str | None = None,
    source_ids: list[str] | None = None,
    mode: str = "discovery",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """axis 为空 → 三条轴与总命中数；否则返回 node 的直接子节点（node 为空 = 该轴顶层）。

    视图无状态：由请求参数完全决定（与 search 的筛选参数同名同义），可以直接编码进 URL。
    """
    req = SearchRequest(
        query=query,
        domain_ids=domain_ids or [],
        topic_ids=topic_ids or [],
        region_ids=region_ids or [],
        region_match=region_match,
        geo_granularity=geo_granularity,
        kinds=kinds_ or [],
        delivery=delivery or [],
        period_start=period_start,
        period_end=period_end,
        frequency=frequency,
        source_ids=source_ids or [],
        mode=mode,
    )
    with guard(), open_catalog() as cat:
        if not axis:
            return axes_overview(cat, req)
        return tree_children(cat, req, axis=axis, node=node or None, limit=limit, offset=offset)
