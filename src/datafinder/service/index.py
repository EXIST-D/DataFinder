"""目录的建立与维护：抓取（build）、一次性迁移旧索引（migrate）。

这是用户主动触发的动作（可能需要几分钟到一个多小时）；抓取串行、限速，遇防护即停并保存进度，可续跑。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from datafinder.catalog import Catalog
from datafinder.catalog.adapters import (
    CAP_DISCOVER,
    CAP_MIGRATE,
    DiscoverOptions,
    adapters,
    get_adapter,
)

from .errors import ServiceError, guard


def _select(ids: list[str] | None, capability: str):
    chosen = [get_adapter(i) for i in ids] if ids else [a for a in adapters().values() if capability in a.capabilities]
    for a in chosen:
        if capability not in a.capabilities:
            raise ServiceError(f"适配器 {a.id} 不支持 {capability}", code="UNSUPPORTED")
    return chosen


async def build(
    adapter_ids: list[str] | None = None, *, params: dict[str, str] | None = None, refresh: bool = False,
    max_requests: int | None = None, progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """逐个适配器抓取目录。params 是适配器私有参数（如 db=3,6 / series=cn,CN-SD / from=2015）。"""
    out = []
    with guard(), Catalog.open() as cat:
        for a in _select(adapter_ids, CAP_DISCOVER):
            opts = DiscoverOptions(params=params or {}, refresh=refresh, max_requests=max_requests, progress=progress)
            out.append({"adapter": a.id, **asdict(await a.discover(cat, opts))})
    return out


def migrate(adapter_ids: list[str] | None = None) -> list[dict[str, Any]]:
    out = []
    with guard(), Catalog.open() as cat:
        for a in _select(adapter_ids, CAP_MIGRATE):
            out.append({"adapter": a.id, **a.migrate(cat)})
    return out
