"""来源适配器的契约与公共工具。

一个来源 = 一个 `Source` 子类 + `sources.yaml` 里的一行配置。
通用协议（如 CKAN）一个类服务多行配置：新增门户只加一行 YAML。

约定：
- 响应结构变了抛 `ShapeError`，**绝不返回空列表**；
- 搜索只返回元数据，不下载数据文件；
- 来源不适用于当前查询时 `supports()` 返回 False（如国家统计局不处理纯英文冷门词）。
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..engine.http import Http
from ..models import Dataset
from ..query.plan import QueryPlan


@dataclass(frozen=True)
class SourceSpec:
    id: str
    title: str
    protocol: str
    status: str = "active"  # active | experimental | disabled
    official: bool = False
    langs: tuple[str, ...] = ("en",)
    base_url: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ctx:
    http: Http


class Source:
    def __init__(self, spec: SourceSpec) -> None:
        self.spec = spec

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def title(self) -> str:
        return self.spec.title

    def supports(self, plan: QueryPlan) -> bool:
        return True

    def cache_query(self, plan: QueryPlan) -> str:
        """决定缓存键的"实际请求内容"。默认：英文检索词。"""
        return plan.query_en()

    async def search(self, ctx: Ctx, plan: QueryPlan, limit: int) -> list[Dataset]:
        raise NotImplementedError


class EnglishSource(Source):
    """用检索词查询的来源：先用较多词，没结果就逐步放宽（4 词 → 2 词 → 1 词）。

    `options.zh_query: true` 时，中文问句**同时**再用中文词查一遍（两路并发、结果合并）：
    基准显示，只发英文词会漏掉中文记录（如《西南八省县级人口普查原始数据》）。
    """

    @property
    def zh_query(self) -> bool:
        return bool(self.spec.options.get("zh_query"))

    def supports(self, plan: QueryPlan) -> bool:
        return bool(plan.query_en()) or (self._zh_enabled(plan))

    def _zh_enabled(self, plan: QueryPlan) -> bool:
        return self.zh_query and plan.lang == "zh" and bool(plan.zh_terms())

    def cache_query(self, plan: QueryPlan) -> str:
        zh = " ".join(plan.zh_terms()) if self._zh_enabled(plan) else ""
        return f"{plan.query_en()}|{zh}"

    async def fetch(self, ctx: Ctx, query: str, limit: int) -> list[Dataset]:
        raise NotImplementedError

    async def search(self, ctx: Ctx, plan: QueryPlan, limit: int) -> list[Dataset]:
        runs = []
        if plan.query_en():
            runs.append(relaxed(plan, lambda q: self.fetch(ctx, q, limit)))
        if self._zh_enabled(plan):
            runs.append(relaxed_zh(plan, lambda q: self.fetch(ctx, q, limit)))
        outcomes = await asyncio.gather(*runs, return_exceptions=True)
        lists = [o for o in outcomes if not isinstance(o, BaseException)]
        if not lists:  # 两路都失败才算失败；只有一路失败时用另一路的结果
            raise next(o for o in outcomes if isinstance(o, BaseException))
        seen: set[str] = set()
        merged: list[Dataset] = []
        for d in itertools.chain.from_iterable(itertools.zip_longest(*lists)):
            if d is not None and d.url not in seen:
                seen.add(d.url)
                merged.append(d)
        return merged


async def relaxed(
    plan: QueryPlan, fetch: Callable[[str], Awaitable[list[Dataset]]]
) -> list[Dataset]:
    tried: set[str] = set()
    for max_terms in (4, 2, 1):
        query = plan.query_en(max_terms=max_terms)
        if not query or query in tried:
            continue
        tried.add(query)
        items = await fetch(query)
        if items:
            return items
    return []


async def relaxed_zh(
    plan: QueryPlan, fetch: Callable[[str], Awaitable[list[Dataset]]]
) -> list[Dataset]:
    tried: set[str] = set()
    for max_terms in (3, 2, 1):
        query = " ".join(plan.zh_terms(max_terms=max_terms))
        if not query or query in tried:
            continue
        tried.add(query)
        items = await fetch(query)
        if items:
            return items
    return []


def clean_text(text: str | None, limit: int = 600) -> str | None:
    """去掉简单 HTML 标签与多余空白，截断。"""
    if not text:
        return None
    import re

    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return (plain[: limit - 1] + "…") if len(plain) > limit else (plain or None)
