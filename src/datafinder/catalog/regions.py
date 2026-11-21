"""地域分面：区域节点、多种代码与名称的互认、上下级关系。

区域 id 沿用仓库已有的 ISO 风格标识（`CN`、`CN-HE`），不另造一套；
同时登记国家标准行政区划码（6 位 gb）与统计用区划代码（12 位，gb + "000000"，统计数据库常用）。
数据见 data/regions.yaml。
"""

from __future__ import annotations

from collections.abc import Iterable
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
    level: str  # world | country | province | special | prefecture | county
    parent_id: str | None
    aliases: tuple[str, ...] = ()
    gb: str | None = None  # 6 位行政区划码
    stat12: str | None = None  # 统计用区划代码（12 位）


# 领域包登记的更细一级区域（地级、县级）：核心不内置，由领域包的数据文件提供
_EXTRA: dict[str, Region] = {}
_NAME_ONLY_IF_UNIQUE = (
    "prefecture",
    "county",
)  # 这两级重名很多（朝阳区、市辖区…），只登记不重名的名称


def register_regions(extra: Iterable[Region]) -> None:
    """登记领域包提供的区域（须带 parent_id，且上级已存在或同批登记）。重复登记同一批是幂等的。"""
    for r in extra:
        _EXTRA[r.id] = r
    _load.cache_clear()


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, Region], dict[str, str]]:
    from .. import domains

    domains.load_all()  # 领域包可能登记更细的区域（plugin 在 load_all 里执行）
    raw = load_yaml("regions.yaml")
    regions: dict[str, Region] = {
        WORLD: Region(WORLD, "全球/国际", "world", None, ("国际", "世界", "全球"))
    }
    for code, info in raw["countries"].items():
        regions[code] = Region(code, info["zh"], "country", WORLD, tuple(info.get("aliases", [])))
    for group, level in (("provinces", "province"), ("special", "special")):
        for code, info in raw.get(group, {}).items():
            gb = info.get("gb")
            regions[code] = Region(
                code,
                info["zh"],
                level,
                COUNTRY_CN,
                tuple(info.get("aliases", [])),
                gb,
                gb + "000000" if gb else None,
            )
    regions.update(_EXTRA)
    index: dict[str, str] = {}
    # 省级与特别行政区先登记：同名时"香港"指 CN-HK（本目录里挂数据的地区），而不是国家词表里的 HK
    ordered = sorted(regions.values(), key=lambda r: r.level not in ("province", "special"))
    for r in ordered:
        if r.level in _NAME_ONLY_IF_UNIQUE:
            continue
        for key in (r.id, r.name_zh, *r.aliases, r.gb, r.stat12):
            if key:
                index.setdefault(normalize(key), r.id)
    # 地级/县级：代码一律可认；名称只有在整个词表里唯一时才认（重名不猜）
    names: dict[str, list[str]] = {}
    for r in regions.values():
        if r.level in _NAME_ONLY_IF_UNIQUE:
            for key in (r.name_zh, *r.aliases):
                names.setdefault(normalize(key), []).append(r.id)
            for key in (r.id, r.gb, r.stat12):
                if key:
                    index.setdefault(normalize(key), r.id)
    for key, ids in names.items():
        if len(set(ids)) == 1:
            index.setdefault(key, ids[0])
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
