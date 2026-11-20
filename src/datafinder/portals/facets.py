"""分类维度：机构类型(tier)、数据形态(kind)、开放程度(openness)。定义在 data/facets.yaml。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ..resources import load_yaml


@dataclass(frozen=True)
class Tier:
    tier: int
    key: str
    zh: str
    official: bool
    owner_types: tuple[str, ...]


@dataclass(frozen=True)
class Kind:
    key: str
    zh: str
    catalog_types: tuple[str, ...]


@dataclass(frozen=True)
class Facets:
    tiers: tuple[Tier, ...]
    kinds: tuple[Kind, ...]
    openness: tuple[tuple[str, str], ...]


@lru_cache(maxsize=1)
def load_facets() -> Facets:
    raw = load_yaml("facets.yaml")
    return Facets(
        tiers=tuple(
            Tier(t["tier"], t["key"], t["zh"], bool(t["official"]), tuple(t["owner_types"]))
            for t in raw["tiers"]
        ),
        kinds=tuple(Kind(k["key"], k["zh"], tuple(k["catalog_types"])) for k in raw["kinds"]),
        openness=tuple((o["key"], o["zh"]) for o in raw["openness"]),
    )


def tier_of(owner_type: str | None) -> int | None:
    """机构类型 → 档位 1–6；未知类型归入 6（社会组织与社区），不留空以免从筛选里消失。"""
    if not owner_type:
        return None
    for t in load_facets().tiers:
        if owner_type in t.owner_types:
            return t.tier
    return 6


def kind_of(catalog_type: str | None) -> str:
    for k in load_facets().kinds:
        if catalog_type in k.catalog_types:
            return k.key
    return "other"


def openness_of(access: str | None) -> str:
    """access 形如 "open" / "open, restricted" / "restricted" / None。"""
    modes = {m.strip() for m in (access or "").split(",") if m.strip()}
    if modes == {"restricted"}:
        return "restricted"
    if "restricted" in modes:
        return "mixed"
    return "open"  # 含未知：上游绝大多数为开放，未知按开放处理会高估，故在界面注明"以原站为准"


def tier_zh(tier: int | None) -> str | None:
    for t in load_facets().tiers:
        if t.tier == tier:
            return t.zh
    return None


def is_official(tier: int | None) -> bool:
    return tier is not None and any(t.tier == tier and t.official for t in load_facets().tiers)
