"""检索单元：把一句话拆成若干"概念单元"，每个单元带同义/相关词（统计术语词表）。

给国家统计局指标索引与年鉴目录共用：对一个候选（指标名/表标题 + 所在分类/章节）打分，
单元整词出现在名称里满分，出现在分类里半分，都没有则按二元组重合度给少量部分分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ..resources import load_yaml
from .plan import parse
from .tokenize import tokenize

# 没有区分度的词：地区范围、时间、"评价/分析"等泛词
_GENERIC = re.compile(
    r"(各省|分省|全国|各地|地区|省份|城市|各市|近[0-9一二三四五六七八九十]+年|历年|年度|评价|体系|分析|研究|情况|水平|"
    r"数据|指标|统计|论文|能力|发展|的|与|和|及)"
)


@dataclass(frozen=True)
class Unit:
    alts: tuple[str, ...]  # 第一个是用户原词，其余是词表给出的相关词
    weight: float = 1.0

    @property
    def bigrams(self) -> frozenset[str]:
        return frozenset(g for a in self.alts for g in tokenize(a) if len(g) == 2)


@lru_cache(maxsize=1)
def _thesaurus() -> dict[str, tuple[str, ...]]:
    raw = load_yaml("thesaurus.zh.yaml")
    return {k: tuple(v) for k, v in raw.items()}


# 多省区域称呼：不是任何一个指标名的组成部分
_AREA_GROUPS = re.compile(
    r"长三角|珠三角|京津冀|长江经济带|黄河流域|粤港澳大湾区|成渝|东北|华北|华东|华南|华中|西南|西北|东部|中部|西部"
)


def _thesaurus_for(word: str) -> tuple[str, ...]:
    """词表里对应这个概念词的相关词：词与词表键相同，或只比键多三个字以内（如"老龄化问题"）。"""
    th = _thesaurus()
    if word in th:
        return th[word]
    for key, syn in th.items():
        if key in word and len(word) <= len(key) + 3:
            return syn
    return ()


def build_units(query: str, *, strip: list[str] | None = None) -> list[Unit]:
    """strip：要先去掉的词（地名、书名等，不属于指标名）。

    先取检索计划里的概念词（保留用户原词，如"固定资产投资"），词表给出相关词；
    词表里的键如果出现在文本里、却没有被任何概念词覆盖（如被切成"经济/增长"的"经济增长"），再补成独立单元。
    """
    plan = parse(query)
    text = query
    for name in [*(strip or []), *plan.location_zh, *plan.regions]:
        text = text.replace(name, " ")
    text = _AREA_GROUPS.sub(" ", text)
    units: list[Unit] = []
    for c in plan.concepts:
        word = c.zh
        if not word or c.dimension or word not in text:
            continue
        cleaned = _GENERIC.sub(" ", _AREA_GROUPS.sub(" ", word)).strip()
        if len(cleaned) < 2:
            continue
        alts = tuple(dict.fromkeys((cleaned, *_thesaurus_for(cleaned))))
        units.append(Unit(alts, 0.5 if c.weak else 1.0))
    for key in sorted(_thesaurus(), key=len, reverse=True):
        if key not in text:
            continue
        if any(key in u.alts[0] for u in units):
            continue  # 已有概念词包含了这个键（它已按词表扩展过）
        # 这个键跨在几个零碎概念上（如"教育资源"被切成"教育/资源"）：用键整体替换被它吞掉的零碎单元
        units = [u for u in units if not (u.alts[0] in key and u.alts[0] != key)]
        units.append(Unit((key, *_thesaurus()[key])))
    return units


def prefilter_terms(units: list[Unit]) -> list[str]:
    """给 SQL LIKE 预筛用的词：所有整词；较长的单元（可能没有整词命中）再加其二元组。"""
    terms: set[str] = set()
    for u in units:
        for a in u.alts:
            if len(a) >= 2:
                terms.add(a)
        if len(u.alts) == 1 and len(u.alts[0]) > 4:
            terms |= set(u.bigrams)
    return sorted(terms)


def unit_scores(units: list[Unit], name: str, extra: str = "") -> list[float]:
    """每个单元各自的得分（0–1）：整词在名称里 1.0，在分类/章节里 0.5，只有二元组部分重合时最多 0.3。"""
    out = []
    name_g = frozenset(tokenize(name))
    for u in units:
        if any(a in name for a in u.alts):
            out.append(1.0)
        elif extra and any(a in extra for a in u.alts):
            out.append(0.5)
        else:
            grams = u.bigrams
            frac = len(grams & name_g) / len(grams) if grams else 0.0
            out.append(0.3 * frac if frac >= 0.5 else 0.0)
    return out


def combine(units: list[Unit], scores: list[float]) -> float:
    """各单元得分按权重平均。"""
    total = sum(u.weight for u in units)
    return sum(u.weight * s for u, s in zip(units, scores, strict=True)) / total if total else 0.0


def unit_score(units: list[Unit], name: str, extra: str = "") -> float:
    return combine(units, unit_scores(units, name, extra))


def balanced_pick(ranked: list, vectors: list[list[float]], limit: int) -> list:
    """先保证每个概念单元至少有一个代表（该单元得分最高且排名最靠前的候选），再按原有顺序补满。

    否则一个条目特别多的概念（如"居民收入"有几十个同类指标）会把另一个概念（"城镇化"）挤出前几名。
    `ranked` 已按总分降序，`vectors` 与之一一对应。
    """
    if not ranked:
        return []
    n_units = len(vectors[0])
    chosen: list[int] = []
    for u in range(n_units):
        best = max(range(len(ranked)), key=lambda i: (vectors[i][u], -i), default=None)
        if best is not None and vectors[best][u] >= 0.5 and best not in chosen:
            chosen.append(best)
    for i in range(len(ranked)):
        if len(chosen) >= limit:
            break
        if i not in chosen:
            chosen.append(i)
    chosen = sorted(chosen[:limit])  # 保持总分顺序展示
    return [ranked[i] for i in chosen]
