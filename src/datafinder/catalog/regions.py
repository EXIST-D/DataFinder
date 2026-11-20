"""地域分面：区域节点、多种代码与名称的互认、上下级关系。

区域 id 沿用仓库已有的 ISO 风格标识（`CN`、`CN-HE`），不另造一套；
同时登记国家标准行政区划码（6 位 gb）与国家统计局发布库使用的 12 位码（gb + "000000"）。
数据见 data/regions.yaml。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ..query.tokenize import normalize
from ..resources import load_yaml

WORLD = "WORLD"
COUNTRY_CN = "CN"


@dataclass(frozen=True)
class Region:
    id: str
    name_zh: str
    level: str  # world | country | province | special
    parent_id: str | None
    aliases: tuple[str, ...] = ()
    gb: str | None = None  # 6 位行政区划码
    stat12: str | None = None  # 国家统计局 12 位码


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, Region], dict[str, str]]:
    raw = load_yaml("regions.yaml")
    regions: dict[str, Region] = {WORLD: Region(WORLD, "全球/国际", "world", None, ("国际", "世界", "全球"))}
    for code, info in raw["countries"].items():
        regions[code] = Region(code, info["zh"], "country", WORLD, tuple(info.get("aliases", [])))
    for group, level in (("provinces", "province"), ("special", "special")):
        for code, info in raw.get(group, {}).items():
            gb = info.get("gb")
            regions[code] = Region(
                code, info["zh"], level, COUNTRY_CN, tuple(info.get("aliases", [])), gb, gb + "000000" if gb else None
            )
    index: dict[str, str] = {}
    for r in regions.values():
        for key in (r.id, r.name_zh, *r.aliases, r.gb, r.stat12):
            if key:
                index.setdefault(normalize(key), r.id)
    return regions, index


def regions() -> dict[str, Region]:
    return _load()[0]


def get(region_id: str) -> Region | None:
    return regions().get(region_id)


def resolve(value: str) -> str | None:
    """区域 id / 中文名 / 别名 / 6 位或 12 位行政区划码 → 区域 id；认不出返回 None（不猜）。"""
    return _load()[1].get(normalize(value.strip()))


def name_zh(region_id: str) -> str:
    r = get(region_id)
    return r.name_zh if r else region_id


def ancestors(region_id: str) -> list[str]:
    out: list[str] = []
    r = get(region_id)
    while r and r.parent_id:
        out.append(r.parent_id)
        r = get(r.parent_id)
    return out


def descendants(region_id: str) -> list[str]:
    kids = [r.id for r in regions().values() if r.parent_id == region_id]
    return kids + [d for k in kids for d in descendants(k)]
