"""目录里的实体：资源（Entry）及其关联（主题、地域、分发入口）。

资源 id 是确定性的组合键 `{source_id}:{kind}:{source_key}`——目录可随时从源头重建，id 必须稳定可复算。
缺失一律 None；未知不等于否。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import kinds


def make_id(source_id: str, kind: str, source_key: str) -> str:
    return f"{source_id}:{kind}:{source_key}"


@dataclass(frozen=True)
class TopicLink:
    topic_id: str
    method: str = "rule"  # rule / inferred / manual
    confidence: float | None = None


@dataclass(frozen=True)
class RegionLink:
    region_id: str
    relation: str = "covers"  # covers / reports_at
    granularity: str | None = None  # country / province / prefecture / county
    scope_mode: str = "unknown"  # exact / descendants / aggregate / unknown
    evidence_status: str = "inferred"  # structure / inferred / verified


@dataclass(frozen=True)
class Distribution:
    access_method: str  # api / direct_file / html_parse / page / manual
    url: str | None = None
    file_format: str | None = None
    usability: str | None = None  # tabular / html_table / document / image
    access_status: str = "unknown"
    note: str | None = None

    @property
    def delivery(self) -> str:
        return kinds.delivery_of(self.access_method)


@dataclass
class Entry:
    source_id: str
    kind: str
    source_key: str
    title: str
    parent_id: str | None = None
    path: str | None = None
    unit: str | None = None
    landing_url: str | None = None
    edition_year: int | None = None
    period_start: int | None = None
    period_end: int | None = None  # start 有值而 end 为空 = 至今
    frequency: str | None = None
    verification_level: str = "unverified"
    access_status: str = "unknown"
    license: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    topics: list[TopicLink] = field(default_factory=list)
    regions: list[RegionLink] = field(default_factory=list)
    distributions: list[Distribution] = field(default_factory=list)

    @property
    def resource_id(self) -> str:
        return make_id(self.source_id, self.kind, self.source_key)

    @property
    def delivery(self) -> str:
        return kinds.best_delivery([d.access_method for d in self.distributions])

    def validate(self) -> None:
        if self.kind not in kinds.kinds():
            raise ValueError(f"未知资源类型 {self.kind!r}")
        if not self.title.strip() or not self.source_key:
            raise ValueError("title 与 source_key 不能为空")
        if self.verification_level not in kinds.verification_levels():
            raise ValueError(f"未知核验等级 {self.verification_level!r}")
        for d in self.distributions:
            if d.access_method not in kinds.access_methods():
                raise ValueError(f"未知访问方式 {d.access_method!r}")
            if d.usability and d.usability not in kinds.usabilities():
                raise ValueError(f"未知 usability {d.usability!r}")
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("观测期起不能晚于止")
