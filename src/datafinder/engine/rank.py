"""排序：RRF 融合 × IDF 加权的概念覆盖度 × 小幅加成。

沿用 Dataseek 经过相关性基准验证的核心思路（ADR 0006/0016）：
1. RRF（k=60）：每个来源的最佳名次贡献 1/(61+rank)；多个来源都找到的靠前。
2. 覆盖度：score × (0.25 + 0.75 × 覆盖权重/总权重)。词权重取 BM25 形式的 IDF，
   df 在本次候选集上统计，所以几乎每条都有的词权重很低。

与 Dataseek 的区别（补中文）：
- 分词用本项目统一的中英分词（中文二元组）；每个"概念"同时有中文说法和英文扩展说法，
  命中任一即算，所以中文记录不会因为"一个词都没命中"被误杀，英文记录也能被中文查询召回。
- 标题命中 1.0，仅描述命中 0.7；维度词（各省/年度…）权重 ×0.3。
所有加成写入 `explain`，`--explain` 可查看。
"""

from __future__ import annotations

import math

from ..models import Dataset
from ..query.plan import Concept, QueryPlan
from ..query.tokenize import has_cjk, token_text, tokenize

RRF_K = 60
TITLE_CREDIT = 1.0
DESC_CREDIT = 0.7
DIMENSION_WEIGHT = 0.3
# 产品取向：中文优先、官方优先。多来源一致（RRF）仍是更强的信号，所以这两个加成保持温和
OFFICIAL_BOOST = 1.3
ZH_BOOST = 1.3


def _in(alt: tuple[str, ...], padded: str) -> bool:
    return f" {' '.join(alt)} " in padded


def _credit(concept: Concept, title: str, desc: str) -> float:
    if any(_in(a, title) for a in concept.alts):
        return TITLE_CREDIT
    if any(_in(a, desc) for a in concept.alts):
        return DESC_CREDIT
    return 0.0


def rank(
    merged: list[Dataset],
    ranks: list[dict[str, int]],
    plan: QueryPlan,
    *,
    official: frozenset[str] = frozenset(),
) -> list[Dataset]:
    n = len(merged)
    if n == 0:
        return []
    titles = [f" {token_text(d.title)} " for d in merged]
    descs = [f" {token_text(d.description or '')} " for d in merged]
    concepts = plan.concepts
    location_phrases = [
        tuple(tokenize(name)) for name in [*plan.location_zh, *plan.location_en] if tokenize(name)
    ]

    # 概念权重：IDF 取自本次候选集
    weights: list[float] = []
    for c in concepts:
        df = sum(1 for t, d in zip(titles, descs, strict=True) if _credit(c, t, d) > 0)
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        weights.append(idf * (DIMENSION_WEIGHT if c.dimension else 1.0))
    total_weight = sum(weights)

    out: list[Dataset] = []
    for d, rank_map, title, desc in zip(merged, ranks, titles, descs, strict=True):
        credits = [_credit(c, title, desc) for c in concepts]
        if concepts and total_weight > 0:
            coverage = sum(w * cr for w, cr in zip(weights, credits, strict=True)) / total_weight
        else:
            coverage = 1.0
        matched_any = any(cr > 0 for cr in credits)
        if concepts and d.description and not matched_any:
            continue  # 有描述却一个概念都没提：多半是来源的松散匹配，丢弃
        rrf = sum(1.0 / (RRF_K + 1 + r) for r in rank_map.values())
        score = rrf * (0.25 + 0.75 * coverage)
        explain: dict[str, float] = {"rrf": rrf, "覆盖度": coverage, "来源数": float(len(rank_map))}

        if rank_map.keys() & official:
            score *= OFFICIAL_BOOST
            explain["官方来源"] = OFFICIAL_BOOST
        if plan.lang == "zh" and has_cjk(d.title):
            score *= ZH_BOOST
            explain["中文资源"] = ZH_BOOST
        if location_phrases and any(_in(p, title) or _in(p, desc) for p in location_phrases):
            score *= 1.2
            explain["地区匹配"] = 1.2
        if d.resources:
            score *= 1.05
            explain["有资源"] = 1.05
        d.score = score
        d.explain = explain
        out.append(d)
    out.sort(key=lambda d: -d.score)
    return out
