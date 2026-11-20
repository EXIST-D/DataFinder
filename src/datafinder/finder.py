"""数据集检索入口：解析查询 → 并发查各来源 → 去重 → 排序。CLI、网页、MCP 共用。

核心是 `stream_search`：每个来源完成就产出一份"当前最佳结果快照"，网页据此逐批显示；
`search_datasets` 只是取最后一份快照，CLI 用它。
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .engine.cache import Cache
from .engine.dedup import merge
from .engine.http import Http
from .engine.orchestrate import SourceStatus, iter_results
from .engine.rank import rank
from .models import Dataset
from .query.plan import QueryPlan, parse
from .sources.base import Ctx, Source
from .sources.registry import build_sources

PER_SOURCE_LIMIT = 20

# 按 host 的最小请求间隔（秒）：礼貌限速。国家统计局是试运行期的政府站点，更保守。
MIN_INTERVAL = {
    "data.stats.gov.cn": 0.4,
    "zenodo.org": 0.5,
    "api.datacite.org": 0.2,
}


@dataclass
class SearchResult:
    plan: QueryPlan
    datasets: list[Dataset]
    statuses: list[SourceStatus]  # 按注册顺序；还没回答的来源状态为 pending
    complete: bool
    candidates: int  # 去重后、排序前的候选数
    final: bool = False  # 所有来源都已回答或被放弃


def _pending(src: Source) -> SourceStatus:
    return SourceStatus(src.id, src.title, "pending", 0, 0, None)  # type: ignore[arg-type]


def rank_with(
    plan: QueryPlan,
    sources: list[Source],
    per_source: dict[str, list[Dataset]],
    **rank_kwargs,
) -> tuple[list[Dataset], int]:
    """对一批检索结果做 去重 + 排序。返回 (排序后列表, 候选数)。

    深拷贝：去重合并会原地改 Dataset，而同一批对象要在多次快照/多种排序方案里复用。
    """
    ordered = {s.id: copy.deepcopy(per_source[s.id]) for s in sources if s.id in per_source}
    merged, ranks = merge(ordered)
    official = frozenset(s.id for s in sources if s.spec.official)
    return rank(merged, ranks, plan, official=official, **rank_kwargs), len(merged)


def _snapshot(
    plan: QueryPlan,
    sources: list[Source],
    per_source: dict[str, list[Dataset]],
    done: dict[str, SourceStatus],
    limit: int,
) -> SearchResult:
    ranked, candidates = rank_with(plan, sources, per_source)
    statuses = [done.get(s.id) or _pending(s) for s in sources]
    final = all(s.id in done for s in sources)
    complete = final and all(st.answered or st.status == "skipped" for st in statuses)
    return SearchResult(plan, ranked[:limit], statuses, complete, candidates, final)


async def stream_search(
    query: str,
    *,
    limit: int = 15,
    only: set[str] | None = None,
    exclude: set[str] | None = None,
    refresh: bool = False,
    use_cache: bool = True,
    deadline: float = 20.0,
) -> AsyncIterator[SearchResult]:
    plan = parse(query)
    sources = build_sources(only=only, exclude=exclude)
    http = Http(min_interval=MIN_INTERVAL)
    cache = Cache() if use_cache else None
    per_source: dict[str, list[Dataset]] = {}
    done: dict[str, SourceStatus] = {}
    try:
        yield _snapshot(plan, sources, per_source, done, limit)  # 初始：全部 pending
        async for items, st in iter_results(
            sources,
            Ctx(http),
            plan,
            limit=PER_SOURCE_LIMIT,
            deadline=deadline,
            cache=cache,
            refresh=refresh,
            explicit=frozenset(only or ()),
        ):
            done[st.id] = st
            if st.answered:
                per_source[st.id] = items
            yield _snapshot(plan, sources, per_source, done, limit)
    finally:
        await http.aclose()
        if cache:
            cache.close()


async def search_datasets(query: str, **kwargs) -> SearchResult:
    last: SearchResult | None = None
    async for snap in stream_search(query, **kwargs):
        last = snap
    assert last is not None
    return last


@dataclass
class Retrieval:
    plan: QueryPlan
    sources: list[Source]
    per_source: dict[str, list[Dataset]]
    statuses: list[SourceStatus]


async def retrieve(
    query: str,
    *,
    only: set[str] | None = None,
    exclude: set[str] | None = None,
    refresh: bool = False,
    use_cache: bool = True,
    deadline: float = 20.0,
) -> Retrieval:
    """只检索、不排序：基准用它对同一批结果比较不同排序方案。"""
    plan = parse(query)
    sources = build_sources(only=only, exclude=exclude)
    http = Http(min_interval=MIN_INTERVAL)
    cache = Cache() if use_cache else None
    per_source: dict[str, list[Dataset]] = {}
    statuses: list[SourceStatus] = []
    try:
        async for items, st in iter_results(
            sources,
            Ctx(http),
            plan,
            limit=PER_SOURCE_LIMIT,
            deadline=deadline,
            cache=cache,
            refresh=refresh,
            explicit=frozenset(only or ()),
        ):
            statuses.append(st)
            if st.answered:
                per_source[st.id] = items
    finally:
        await http.aclose()
        if cache:
            cache.close()
    return Retrieval(plan, sources, per_source, statuses)
