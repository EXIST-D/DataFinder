"""来源注册表：读 sources.yaml，构造适配器实例。构造时校验，配置写错立刻报错。"""

from __future__ import annotations

from ..resources import load_yaml
from .base import Source, SourceSpec
from .ckan import CkanSource
from .datacite import DataCiteSource
from .dataseek import DataseekSource
from .nbs import NbsSource
from .zenodo import ZenodoSource

PROTOCOLS: dict[str, type[Source]] = {
    "nbs": NbsSource,
    "zenodo": ZenodoSource,
    "datacite": DataCiteSource,
    "ckan": CkanSource,
    "dataseek": DataseekSource,
}
_STATUSES = {"active", "experimental", "disabled"}


def load_specs() -> list[SourceSpec]:
    specs: list[SourceSpec] = []
    seen: set[str] = set()
    for row in load_yaml("sources.yaml"):
        spec = SourceSpec(
            id=row["id"],
            title=row["title"],
            protocol=row["protocol"],
            status=row.get("status", "active"),
            official=bool(row.get("official", False)),
            langs=tuple(row.get("langs", ["en"])),
            base_url=row.get("base_url"),
            options=dict(row.get("options") or {}),
        )
        if spec.id in seen:
            raise ValueError(f"sources.yaml 里来源 id 重复：{spec.id}")
        if spec.protocol not in PROTOCOLS:
            raise ValueError(f"来源 {spec.id} 使用了未知协议 {spec.protocol!r}")
        if spec.status not in _STATUSES:
            raise ValueError(f"来源 {spec.id} 的 status 非法：{spec.status!r}")
        seen.add(spec.id)
        specs.append(spec)
    return specs


def build_sources(*, only: set[str] | None = None, exclude: set[str] | None = None) -> list[Source]:
    """构造启用的来源。`only` 为用户点名（即使 disabled 也启用）；`exclude` 为排除。"""
    specs = load_specs()
    ids = {s.id for s in specs}
    unknown = ((only or set()) | (exclude or set())) - ids
    if unknown:
        raise ValueError(f"未知来源：{', '.join(sorted(unknown))}。可选：{', '.join(sorted(ids))}")
    active = [
        s
        for s in specs
        if (s.id in only if only else s.status != "disabled") and s.id not in (exclude or set())
    ]
    # Dataseek 只需要排除"本次实际启用的、且在 Dataseek 里也有的"自研来源
    native_dsk = [
        str(s.options["dsk_id"])
        for s in active
        if s.protocol != "dataseek" and s.options.get("dsk_id")
    ]
    out: list[Source] = []
    for s in active:
        cls = PROTOCOLS[s.protocol]
        out.append(cls(s, exclude=native_dsk) if cls is DataseekSource else cls(s))  # type: ignore[call-arg]
    return out
