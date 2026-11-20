"""分类规则引擎：用"关键词 → 主题/地域"的规则给资源打标签。规则数据由领域包提供（YAML）。

规则作用的文本有三种范围（scope）：
- top    顶层分类名/章名（如国家统计局的"工业"、年鉴的"十三、工业"）
- path   路径上的任一级分类名
- title  资源自己的标题
每条规则可多标签；`under` 表示只在已经命中某个父主题时才生效（用于二级主题）；`none` 为排除词。
命中依据一律记录为 method=rule，不冒充人工核验。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_NUMBERING = re.compile(
    r"^(?:[（(]?[一二三四五六七八九十百零]+[）)、.．]\s*|第[一二三四五六七八九十百零\d]+[篇章部分]\s*)"
)
_SPACES = re.compile(r"[\s\u3000\xa0]+")


def normalize_label(text: str) -> str:
    """去掉章号（"十三、"/"第十四篇"）和空白，便于按名称匹配。"""
    return _SPACES.sub("", _NUMBERING.sub("", text.strip()))


@dataclass(frozen=True)
class TopicRule:
    scope: str
    topics: tuple[str, ...]
    any: tuple[str, ...]
    none: tuple[str, ...] = ()
    under: str | None = None


@dataclass(frozen=True)
class RegionRule:
    scope: str
    any: tuple[str, ...]
    region: str | None  # None：不指定具体区域，只说明报告粒度（如"各县"）
    relation: str = "covers"  # covers | reports_at
    granularity: str | None = None
    scope_mode: str = "unknown"  # exact | descendants | aggregate | unknown


@dataclass(frozen=True)
class RegionTag:
    region: str | None
    relation: str
    granularity: str | None
    scope_mode: str
    evidence_status: str = "inferred"


@dataclass
class Target:
    """被分类的对象：只需要这三类文本。"""

    top: str = ""
    path: list[str] = field(default_factory=list)
    title: str = ""

    def texts(self, scope: str) -> list[str]:
        if scope == "top":
            return [normalize_label(self.top)] if self.top else []
        if scope == "path":
            return [normalize_label(p) for p in self.path]
        return [normalize_label(self.title)] if self.title else []


def _hit(rule_any: tuple[str, ...], rule_none: tuple[str, ...], texts: list[str]) -> bool:
    return any(any(k in t for k in rule_any) and not any(x in t for x in rule_none) for t in texts)


@dataclass(frozen=True)
class RuleSet:
    topic_rules: tuple[TopicRule, ...] = ()
    region_rules: tuple[RegionRule, ...] = ()

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> RuleSet:
        topic_rules = tuple(
            TopicRule(
                r.get("scope", "top"),
                tuple(r["topics"]),
                tuple(r["any"]),
                tuple(r.get("none", ())),
                r.get("under"),
            )
            for r in raw.get("topic_rules", [])
        )
        region_rules = tuple(
            RegionRule(
                r.get("scope", "top"),
                tuple(r["any"]),
                r.get("region"),
                r.get("relation", "covers"),
                r.get("granularity"),
                r.get("scope_mode", "unknown"),
            )
            for r in raw.get("region_rules", [])
        )
        return cls(topic_rules, region_rules)

    def topics(self, target: Target) -> list[str]:
        """命中的主题 id（去重、保序）。`under` 规则按声明顺序在其父主题之后判断。"""
        found: list[str] = []
        for r in self.topic_rules:
            if r.under and r.under not in found:
                continue
            if _hit(r.any, r.none, target.texts(r.scope)):
                found += [t for t in r.topics if t not in found]
        return found

    def regions(self, target: Target) -> list[RegionTag]:
        out: list[RegionTag] = []
        for r in self.region_rules:
            if _hit(r.any, (), target.texts(r.scope)):
                tag = RegionTag(r.region, r.relation, r.granularity, r.scope_mode)
                if tag not in out:
                    out.append(tag)
        return out
