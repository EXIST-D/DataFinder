"""资源类型体系（kind / access_method / file_format / usability / delivery）。数据见 data/taxonomy/resource_kinds.yaml。"""

from __future__ import annotations

from functools import lru_cache

from ..resources import load_yaml


@lru_cache(maxsize=1)
def _raw() -> dict:
    return load_yaml("taxonomy", "resource_kinds.yaml")


def kinds() -> dict[str, dict]:
    return {k["id"]: k for k in _raw()["kinds"]}


def searchable_kinds() -> list[str]:
    """默认参与检索的类型（导航用的 category 除外）。"""
    return [k for k, v in kinds().items() if not v.get("navigational")]


def container_kinds() -> set[str]:
    return {k for k, v in kinds().items() if v.get("container")}


def access_methods() -> dict[str, dict]:
    return _raw()["access_methods"]


def file_formats() -> list[str]:
    return list(_raw()["file_formats"])


def usabilities() -> list[str]:
    return list(_raw()["usability"])


def verification_levels() -> list[str]:
    return list(_raw()["verification_levels"])


def access_statuses() -> list[str]:
    return list(_raw()["access_statuses"])


def delivery_of(access_method: str) -> str:
    """分发入口的访问方式 → 用户看到的获取方式（download / generate / link）。"""
    return access_methods()[access_method]["delivery"]


def best_delivery(access_methods_: list[str]) -> str:
    """一个资源可有多个分发入口：取最方便的一类（download > generate > link）；没有入口则 link。"""
    order = {"download": 0, "generate": 1, "link": 2}
    return min((delivery_of(m) for m in access_methods_), key=order.__getitem__, default="link")
