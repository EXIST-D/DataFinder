"""行业打标：规则 + 关键词，可复现、可手工修正，不使用 AI。

匹配方式（快）：文本先切成 ASCII 单词集合；
- 单词关键词：集合查找（等价于带单词边界的匹配）
- 多词关键词：在"单词序列"里做短语子串匹配
- 中文关键词：在原文里做子串匹配
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ..query.tokenize import normalize
from ..resources import load_yaml

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Industry:
    id: str
    zh: str
    en: str
    words: frozenset[str]
    phrases: tuple[str, ...]
    cjk: tuple[str, ...]

    def hits(self, text: str, words: frozenset[str], sequence: str) -> int:
        n = len(self.words & words)
        n += sum(1 for p in self.phrases if f" {p} " in sequence)
        n += sum(1 for k in self.cjk if k in text)
        return n


@dataclass(frozen=True)
class IndustryConfig:
    industries: dict[str, Industry]
    min_hits: int
    topic_map: dict[str, tuple[str, ...]]
    catalog_types: dict[str, dict]


def _build(raw: dict) -> Industry:
    words: set[str] = set()
    phrases: list[str] = []
    cjk: list[str] = []
    for kw in raw["keywords"]:
        kw = normalize(kw)
        if not kw.isascii():
            cjk.append(kw)
        elif " " in kw:
            phrases.append(kw)
        else:
            words.add(kw)
    return Industry(raw["id"], raw["zh"], raw["en"], frozenset(words), tuple(phrases), tuple(cjk))


@lru_cache(maxsize=1)
def load_config() -> IndustryConfig:
    raw = load_yaml("industries.yaml")
    return IndustryConfig(
        industries={i["id"]: _build(i) for i in raw["industries"]},
        min_hits=int(raw.get("min_hits", 2)),
        topic_map={k: tuple(v) for k, v in raw["topic_map"].items()},
        catalog_types=raw["catalog_types"],
    )


def tag_industries(
    *, name: str, description: str, tags: list[str], topics: list[str], limit: int = 4
) -> list[str]:
    cfg = load_config()
    scores: dict[str, int] = {}
    for topic in topics:
        for ind in cfg.topic_map.get(topic, ()):
            scores[ind] = scores.get(ind, 0) + 10
    text = normalize(" \n ".join([name, description, *tags]))
    tokens = _WORD.findall(text)
    words = frozenset(tokens)
    sequence = f" {' '.join(tokens)} "
    for ind in cfg.industries.values():
        hits = ind.hits(text, words, sequence)
        if hits >= cfg.min_hits:
            scores[ind.id] = scores.get(ind.id, 0) + hits
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [i for i, _ in ranked[:limit]]


def industry_zh(ind_id: str) -> str:
    ind = load_config().industries.get(ind_id)
    return ind.zh if ind else ind_id
