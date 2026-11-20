"""数据集检索入口：解析查询 → 并发查各来源 → 去重 → 排序。CLI、网页、MCP 共用。"""

from __future__ import annotations

from dataclasses import dataclass

from .engine.cache import Cache
from .engine.dedup import merge
from .engine.http import Http
from .engine.orchestrate import SourceStatus, run_search
from .engine.rank import rank
from .models import Dataset
from .query.plan import QueryPlan, parse
from .sources.base import Ctx
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
    statuses: list[SourceStatus]
    complete: bool
    candidates: int  # 去重后、排序前的候选数


async def search_datasets(
    query: str,
    *,
    limit: int = 15,
    only: set[str] | None = None,
    exclude: set[str] | None = None,
    refresh: bool = False,
    use_cache: bool = True,
    deadline: float = 20.0,
) -> SearchResult:
    plan = parse(query)
    sources = build_sources(only=only, exclude=exclude)
    http = Http(min_interval=MIN_INTERVAL)
    cache = Cache() if use_cache else None
    try:
        outcome = await run_search(
            sources,
            Ctx(http),
            plan,
            limit=PER_SOURCE_LIMIT,
            deadline=deadline,
            cache=cache,
            refresh=refresh,
            explicit=frozenset(only or ()),
        )
    finally:
        await http.aclose()
        if cache:
            cache.close()
    merged, ranks = merge(outcome.per_source)
    official = frozenset(s.id for s in sources if s.spec.official)
    ranked = rank(merged, ranks, plan, official=official)
    return SearchResult(plan, ranked[:limit], outcome.statuses, outcome.complete, len(merged))
