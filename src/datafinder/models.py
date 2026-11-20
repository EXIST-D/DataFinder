"""数据集 / 资源模型。

约定（贯穿全项目）：
- 缺失一律 None，界面显示"未知"；不推测、不编造。
- `period`（数据覆盖的时间）、`updated`（上游更新时间）是两个概念，不混用。
- `license` 未知时不暗示可商用。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Resource:
    kind: str  # file | api | table | page
    name: str
    url: str
    format: str | None = None
    size_bytes: int | None = None
    note: str | None = None  # 调用说明（方法、请求头等）
    body: str | None = None  # 可直接复制的请求体（如 JSON）


@dataclass
class Dataset:
    title: str
    url: str
    source: str  # 首个来源 id（合并后以第一个为准）
    landing_url: str | None = None  # 给人点击的页面（url 只做身份标识时，如国家统计局单页应用）
    description: str | None = None
    publisher: str | None = None
    doi: str | None = None
    license: str | None = None
    updated: str | None = None  # 上游更新时间
    period: str | None = None  # 数据覆盖时间（如 "2024年"）
    formats: list[str] = field(default_factory=list)
    size_bytes: int | None = None
    resources: list[Resource] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)  # 其他 DOI（版本、concept DOI 等）
    sources: list[str] = field(default_factory=list)  # 合并后：所有找到它的来源
    # 以下由排序阶段填写
    score: float = 0.0
    explain: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Dataset:
        data = dict(data)
        data["resources"] = [Resource(**r) for r in data.get("resources", [])]
        return cls(**data)
