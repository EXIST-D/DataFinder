"""统一检索：关键词（检索单元 + 术语词表）× 主题 × 地域 × 资源类型 × 获取方式 × 观测期。

两阶段：SQL 按分面与关键词预筛候选 → Python 用检索单元打分、同模板去重、跨版次归并、概念均衡后取页。
分面计数基于"当前候选集合"（关键词预筛 + 其他分面的筛选），不是整库条数。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..query.units import Unit, balanced_pick, build_units, combine, unit_scores
from . import kinds, regions, taxonomy
from .entities import Entry
from .query import DELIVERY_BY_RANK, InvalidFacet, SearchRequest, Where, delivery_rank_sql
from .repository import Catalog
from .text import norm_title

PER_PARENT = 2  # 同一父级（数据集）下最多出几个指标：避免同模板指标刷屏
KIND_PREFERENCE = {
    "indicator": 0.0, "table": 0.0, "dataset": -0.02, "bulletin": -0.02, "database": -0.04,
    "series": -0.04, "edition": -0.04, "portal": -0.05,
}
FACET_TOP = 60
DELIVERY_ZH = {"download": "可直接下载", "generate": "需要生成", "link": "只有链接"}


@dataclass
class SearchResponse:
    request: SearchRequest
    total: int
    results: list[dict[str, Any]]
    facets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        r = self.request
        return {
            "query": r.query, "mode": r.mode, "total": self.total, "returned": len(self.results),
            "offset": r.offset, "results": self.results, "facets": self.facets, "hints": self.hints,
        }


# ── 单条结果的整理 ──


def summarize(e: Entry, *, units: list[Unit], req: SearchRequest, score: float,
              editions: list[Entry] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "resource_id": e.resource_id, "kind": e.kind, "title": e.title, "source_id": e.source_id,
        "path": e.path, "parent_id": e.parent_id, "unit": e.unit,
        "topic_ids": [t.topic_id for t in e.topics],
        "regions": [
            {"region_id": r.region_id, "relation": r.relation, "granularity": r.granularity,
             "scope_mode": r.scope_mode, "evidence": r.evidence_status}
            for r in e.regions
        ],
        "period": {"start": e.period_start, "end": e.period_end,
                   "ongoing": bool(e.period_start and not e.period_end), "edition_year": e.edition_year},
        "frequency": e.frequency, "delivery": e.delivery,
        "usability": next((d.usability for d in e.distributions if d.usability), None),
        "verification_level": e.verification_level, "access_status": e.access_status,
        "landing_url": e.landing_url, "score": round(score, 3),
        "why_matched": _why(e, units, req), "missing_requirements": _missing(e, req),
    }
    if editions:
        item["editions"] = [
            {"resource_id": x.resource_id, "edition_year": x.edition_year, "title": x.title,
             "url": next((d.url for d in x.distributions if d.url), x.landing_url),
             "file_format": next((d.file_format for d in x.distributions if d.file_format), None)}
            for x in editions
        ]
        item["versions"] = [x.edition_year for x in editions]
    return item


def _why(e: Entry, units: list[Unit], req: SearchRequest) -> list[str]:
    out = []
    for u in units:
        hit, where = next(((a, "标题") for a in u.alts if a in e.title), (None, ""))
        if not hit:
            hit, where = next(((a, "分类路径") for a in u.alts if a in (e.path or "")), (None, ""))
        if hit:
            out.append(f"{where}含“{hit}”" if hit == u.alts[0] else f"{where}含“{hit}”（“{u.alts[0]}”的相关词）")
    for tid in req.topic_ids:
        if any(t.topic_id == tid or tid in taxonomy.ancestors(t.topic_id) for t in e.topics):
            out.append(f"主题：{taxonomy.get(tid).name_zh}")  # type: ignore[union-attr]
    out += [f"地域：{regions.name_zh(rid)}" for rid in req.region_ids]
    return out


def _missing(e: Entry, req: SearchRequest) -> list[str]:
    """宽松模式下，哪些要求只是"没有信息"而非"确认满足"。"""
    out = []
    if req.region_ids and not e.regions:
        out.append("地域未标注")
    if req.geo_granularity:
        grans = [r for r in e.regions if r.granularity == req.geo_granularity]
        if not grans:
            out.append(f"未标注{req.geo_granularity}级粒度")
        elif not any(r.evidence_status in ("structure", "verified") for r in grans):
            out.append(f"{req.geo_granularity}级粒度由标题推断，未验证")
    if (req.period_start or req.period_end) and e.period_start is None:
        out.append("观测期未知" + (f"（仅有出版年 {e.edition_year}）" if e.edition_year else ""))
    if req.frequency and not e.frequency:
        out.append("频率未知")
    return out


# ── 主入口 ──


def search(cat: Catalog, request: SearchRequest) -> SearchResponse:
    req = request.normalized()
    units = build_units(req.query) if req.query.strip() else []
    if req.query.strip() and not units:
        return SearchResponse(req, 0, [], {}, ["关键词里只有“数据/统计/评价”这类无区分度的词，请换成具体的指标或主题词"])
    where = Where(req, units)
    clause, args = where.sql()
    total = cat.conn.execute(f"SELECT COUNT(*) FROM resources r WHERE {clause}", args).fetchone()[0]

    if units:
        results = _materialize(cat, _page(_rank(cat, clause, args, units, req), req), units, req)
    else:  # 纯分面浏览：SQL 直接排序分页
        rows = cat.conn.execute(
            f"SELECT r.* FROM resources r WHERE {clause} ORDER BY r.kind, r.title LIMIT ? OFFSET ?",
            [*args, req.limit, req.offset],
        ).fetchall()
        results = [summarize(e, units=[], req=req, score=0.0) for e in cat.hydrate_rows(rows)]

    hints: list[str] = []
    if req.mode == "strict":
        relaxed = SearchRequest(**{**req.__dict__, "mode": "discovery"})
        c2, a2 = Where(relaxed, units).sql()
        extra = cat.conn.execute(f"SELECT COUNT(*) FROM resources r WHERE {c2}", a2).fetchone()[0] - total
        if extra > 0:
            hints.append(f"strict 模式排除了 {extra} 条“信息未知/未验证”的候选；mode=discovery 可以看到它们（会标注缺什么）")
    return SearchResponse(req, total, results, _facets(cat, where) if req.include_facets else {}, hints)


@dataclass
class _Cand:
    row: sqlite3.Row
    score: float
    vec: list[float]
    versions: list[sqlite3.Row] = field(default_factory=list)


def _rank(cat: Catalog, clause: str, args: list, units: list[Unit], req: SearchRequest) -> list[_Cand]:
    cands: list[_Cand] = []
    for row in cat.conn.execute(f"SELECT r.* FROM resources r WHERE {clause}", args):
        vec = unit_scores(units, row["title"], row["path"] or "")
        s = combine(units, vec)
        if s > 0:
            cands.append(_Cand(row, s + KIND_PREFERENCE.get(row["kind"], 0.0) - 0.0005 * min(len(row["title"]), 80), vec))
    cands.sort(key=lambda c: -c.score)
    if req.group_versions:
        cands = _merge_versions(cands)
    per_parent: dict[str, int] = {}
    kept: list[_Cand] = []
    for c in cands:
        if c.row["kind"] == "indicator" and c.row["parent_id"]:
            n = per_parent.get(c.row["parent_id"], 0)
            if n >= PER_PARENT:
                continue
            per_parent[c.row["parent_id"]] = n + 1
        kept.append(c)
    return kept


def _merge_versions(cands: list[_Cand]) -> list[_Cand]:
    """同一张表在不同版次里是不同资源：归并成一条，列出各版次（代表取最新版次）。"""
    groups: dict[tuple, _Cand] = {}
    out: list[_Cand] = []
    for c in cands:
        if c.row["kind"] != "table" or c.row["edition_year"] is None:
            out.append(c)
            continue
        key = (c.row["source_id"], norm_title(c.row["title"]))
        g = groups.get(key)
        if g is None:
            c.versions = [c.row]
            groups[key] = c
            out.append(c)
            continue
        g.versions.append(c.row)
        g.vec = [max(a, b) for a, b in zip(g.vec, c.vec, strict=True)]
        if c.row["edition_year"] > g.row["edition_year"]:
            g.row = c.row
    return out


def _page(ranked: list[_Cand], req: SearchRequest) -> list[_Cand]:
    if req.offset == 0:
        return balanced_pick(ranked, [c.vec for c in ranked], req.limit)
    return ranked[req.offset : req.offset + req.limit]


def _materialize(cat: Catalog, page: list[_Cand], units: list[Unit], req: SearchRequest) -> list[dict[str, Any]]:
    entries = {e.resource_id: e for e in cat.hydrate_rows([c.row for c in page] + [v for c in page for v in c.versions])}
    out = []
    for c in page:
        eds = sorted((entries[v["resource_id"]] for v in c.versions), key=lambda x: -(x.edition_year or 0)) or None
        out.append(summarize(entries[c.row["resource_id"]], units=units, req=req, score=c.score, editions=eds))
    return out


# ── 分面计数 ──


def _facets(cat: Catalog, where: Where) -> dict[str, list[dict[str, Any]]]:
    def counts(skip: str, select: str, join: str = "", distinct: bool = False) -> list[sqlite3.Row]:
        # 主题/类型/获取方式每个资源在一个分组里至多出现一次，不需要 DISTINCT（省时）；地域因 relation 可能重复
        clause, args = where.sql(skip=(skip,))
        count = "COUNT(DISTINCT r.resource_id)" if distinct else "COUNT(*)"
        return cat.conn.execute(
            f"SELECT {select} AS id, {count} AS n FROM resources r {join}"
            f" WHERE {clause} GROUP BY id ORDER BY n DESC LIMIT {FACET_TOP}", args,
        ).fetchall()

    topics = []
    for r in counts("topic", "rt.topic_id", "JOIN resource_topics rt ON rt.resource_id = r.resource_id"):
        t = taxonomy.get(r["id"])
        if t and not t.reserved:
            topics.append({"id": t.id, "name": t.name_zh, "count": r["n"], "level": t.level, "parent_id": t.parent_id})
    regs = [{"id": r["id"], "name": regions.name_zh(r["id"]), "count": r["n"]}
            for r in counts("region", "rr.region_id", "JOIN resource_regions rr ON rr.resource_id = r.resource_id", True)]
    ks = [{"id": r["id"], "name": kinds.kinds()[r["id"]]["zh"], "count": r["n"]}
          for r in counts("kind", "r.kind") if r["id"] in kinds.kinds()]
    dl = [{"id": DELIVERY_BY_RANK[r["id"]], "name": DELIVERY_ZH[DELIVERY_BY_RANK[r["id"]]], "count": r["n"]}
          for r in counts("delivery", delivery_rank_sql())]
    return {"topics": topics, "regions": regs, "kinds": ks, "delivery": dl}


def facet_counts(cat: Catalog, request: SearchRequest | None = None) -> dict[str, list[dict[str, Any]]]:
    """只要分面计数（不取结果）。"""
    req = (request or SearchRequest()).normalized()
    units = build_units(req.query) if req.query.strip() else []
    return _facets(cat, Where(req, units))


__all__ = ["InvalidFacet", "SearchRequest", "SearchResponse", "facet_counts", "search", "summarize"]
