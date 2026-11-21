"""目录的建立与维护：抓取（build）、一次性迁移旧索引（migrate）、快照的制作与安装（snapshot / pull）。

这是用户主动触发的动作（可能需要几分钟到一个多小时）；抓取串行、限速，遇防护即停并保存进度，可续跑。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from datafinder.catalog import Catalog
from datafinder.catalog.adapters import (
    CAP_DISCOVER,
    CAP_MIGRATE,
    DiscoverOptions,
    adapters,
    get_adapter,
)
from datafinder.catalog.snapshot import (
    DEFAULT_BASE_URL,
    build_snapshot,
    install_snapshot,
)

from .errors import ServiceError, guard


def _select(ids: list[str] | None, capability: str):
    chosen = (
        [get_adapter(i) for i in ids]
        if ids
        else [a for a in adapters().values() if capability in a.capabilities]
    )
    for a in chosen:
        if capability not in a.capabilities:
            raise ServiceError(f"适配器 {a.id} 不支持 {capability}", code="UNSUPPORTED")
    return chosen


async def build(
    adapter_ids: list[str] | None = None,
    *,
    params: dict[str, str] | None = None,
    refresh: bool = False,
    max_requests: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """逐个适配器抓取目录。params 是适配器私有参数（如 db=3,6 / series=cn,CN-SD / from=2015）。"""
    out = []
    with guard(), Catalog.open() as cat:
        for a in _select(adapter_ids, CAP_DISCOVER):
            opts = DiscoverOptions(
                params=params or {}, refresh=refresh, max_requests=max_requests, progress=progress
            )
            out.append({"adapter": a.id, **asdict(await a.discover(cat, opts))})
    return out


def migrate(adapter_ids: list[str] | None = None) -> list[dict[str, Any]]:
    out = []
    with guard(), Catalog.open() as cat:
        for a in _select(adapter_ids, CAP_MIGRATE):
            out.append({"adapter": a.id, **a.migrate(cat)})
    return out


async def probe(url: str, *, editions: int = 3) -> dict[str, Any]:
    """扫一个统计局站点：有哪些数据栏目、年鉴有哪些版次、目录形态能不能解析。

    维护者用：新省接入前先跑这个，把"要不要手写解析器"变成一次可复现的判断。
    """
    from datafinder.catalog.adapters import adapters
    from datafinder.catalog.scout import has_probe, probe_site

    adapters()  # 触发领域包加载（它们在这里注册探测扩展点）
    if not has_probe():
        raise ServiceError("站点探测未注册（领域插件没加载）", code="UNSUPPORTED")
    with guard():
        return await probe_site(url, editions=editions)


def snapshot(out_dir: str | Path) -> dict[str, Any]:
    """把当前目录库打包成可发布的快照（维护者用）。返回清单。"""
    with guard():
        return build_snapshot(Path(out_dir).expanduser())


def pull(
    source: str | None = None,
    *,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """安装发布的目录快照（默认官方发布；source 可以是 https 基地址或含快照文件的本地目录）。"""
    with guard():
        return install_snapshot(
            source or DEFAULT_BASE_URL, force=force, progress=progress, transport=transport
        )
