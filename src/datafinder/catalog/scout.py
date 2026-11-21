"""站点探测的扩展点：核心只认"给一个网址，回来一份报告"，具体怎么扫由领域包注册。

省级统计站点长什么样、目录有哪些形态，是领域知识，放在
`domains/official_statistics_cn/scout.py`；核心这边只留一个注册表，
免得 CLI / service 直接 import 领域子模块（分层测试会拦）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

ProbeFn = Callable[..., Awaitable[dict[str, Any]]]

_PROBE: ProbeFn | None = None


def register_probe(fn: ProbeFn) -> None:
    global _PROBE
    _PROBE = fn


def has_probe() -> bool:
    return _PROBE is not None


async def probe_site(url: str, *, editions: int = 3) -> dict[str, Any]:
    if _PROBE is None:
        raise RuntimeError("没有注册站点探测（domain 插件未加载）")
    return await _PROBE(url, editions=editions)
