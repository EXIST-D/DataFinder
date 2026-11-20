"""领域包的网页接口注册表：领域在 plugin 里登记一个"路由工厂"，核心建 app 时才调用（避免无谓导入 FastAPI）。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter

from .. import domains

_FACTORIES: list[Callable[[], APIRouter]] = []


def register_router(factory: Callable[[], APIRouter]) -> None:
    _FACTORIES.append(factory)


def build_all() -> list[APIRouter]:
    domains.load_all()
    return [f() for f in _FACTORIES]
