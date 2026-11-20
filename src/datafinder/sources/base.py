"""来源适配器的契约与公共工具。

一个来源 = 一个 `Source` 子类 + `sources.yaml` 里的一行配置。
通用协议（如 CKAN）一个类服务多行配置：新增门户只加一行 YAML。

约定：
- 响应结构变了抛 `ShapeError`，**绝不返回空列表**；
- 搜索只返回元数据，不下载数据文件；
- 来源不适用于当前查询时 `supports()` 返回 False（如国家统计局不处理纯英文冷门词）。
"""

from __future__ import annotations

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
    """用英文检索词的来源：先用较多词，没结果就逐步放宽（4 词 → 2 词 → 1 词）。"""

    def supports(self, plan: QueryPlan) -> bool:
        return bool(plan.query_en())

    async def fetch(self, ctx: Ctx, query: str, limit: int) -> list[Dataset]:
        raise NotImplementedError

    async def search(self, ctx: Ctx, plan: QueryPlan, limit: int) -> list[Dataset]:
        return await relaxed(plan, lambda q: self.fetch(ctx, q, limit))


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


def clean_text(text: str | None, limit: int = 600) -> str | None:
    """去掉简单 HTML 标签与多余空白，截断。"""
    if not text:
        return None
    import re

    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return (plain[: limit - 1] + "…") if len(plain) > limit else (plain or None)
