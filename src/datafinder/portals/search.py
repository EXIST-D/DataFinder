"""门户检索：FTS5 召回 + 概念覆盖度 + 行业加分 + 小幅加权。

score = (bm25 + 行业加分) × (0.25 + 0.75 × 概念覆盖度) × 加成

- 概念覆盖度：查询里有几个"概念"被命中。每个概念可有中英多种说法，命中任一说法算 1 分；
  **门户带有该概念所属行业的标签**（但文字没出现）算 0.5 分（门户描述里不会出现"GDP"，
  但"宏观经济"标签的统计门户显然相关；不过字面命中应强于推断命中）。
- 行业加分：门户行业标签与查询行业的交集数 × 8，让带标签的精选门户即使文本命中弱也能浮上来。
- 加成：精选、国家级、地区/国家匹配、中文查询时略偏中国来源、问 API 时偏有 API 的。
- 候选召回：全局 bm25 前 300 条 ∪ 查询里指明的地区/国家内的前 300 条 ∪ 精选门户，
  避免"地区加分"因候选截断而失效。

所有分项都写进 `explain`，可用 `--explain` 查看为什么这条排前面。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from ..query.plan import QueryPlan, parse

# bm25 列权重：name, name_zh, description, hints
_WEIGHTS = "6.0, 6.0, 1.0, 2.0"
_CANDIDATES = 300
_INDUSTRY_POINTS = 8.0


@dataclass
class PortalHit:
    row: sqlite3.Row
    score: float
    coverage: float
    matched: list[str] = field(default_factory=list)
    explain: dict[str, float] = field(default_factory=dict)

    @property
    def industries(self) -> list[str]:
        return json.loads(self.row["industries"])


def _alt_in(alt: tuple[str, ...], padded: str) -> bool:
    return f" {' '.join(alt)} " in padded


def _boosts(plan: QueryPlan, row: sqlite3.Row) -> dict[str, float]:
    b: dict[str, float] = {}
    if row["featured"]:
        b["精选"] = 1.8
    if row["is_national"]:
        b["国家级"] = 1.15
    if plan.regions:
        # 明确说了省份：本省压倒一切；同国家其他省只略加分
        if row["region"] in plan.regions:
            b["地区匹配"] = 10.0
        elif row["country"] in plan.countries:
            b["同国其他地区"] = 1.5
    elif plan.countries and row["country"] in plan.countries:
        b["国家匹配"] = 3.0
    elif plan.lang == "zh" and row["country"] == "CN" and not plan.countries:
        b["中文查询偏中国"] = 1.3
    if row["has_api"]:
        b["有API"] = 1.3 if "api" in plan.formats else 1.03
    return b


def _filters(
    country: str | None, type_: str | None, industry: str | None, api_only: bool
) -> tuple[str, list]:
    clauses: list[str] = []
    args: list = []
    if country:
        clauses.append("p.country = ?")
        args.append(country.upper())
    if type_:
        clauses.append("p.catalog_type = ?")
        args.append(type_)
    if industry:
        clauses.append("p.industries LIKE ?")
        args.append(f'%"{industry}"%')
    if api_only:
        clauses.append("p.has_api = 1")
    return ("".join(f" AND {c}" for c in clauses)), args


def _location_clause(plan: QueryPlan) -> tuple[str, list]:
    if plan.regions:
        return f" AND p.region IN ({','.join('?' * len(plan.regions))})", list(plan.regions)
    if plan.countries:
        return f" AND p.country IN ({','.join('?' * len(plan.countries))})", list(plan.countries)
    return "", []


def _list_by_conditions(
    conn: sqlite3.Connection, plan: QueryPlan, where: str, args: list, limit: int
) -> list[PortalHit]:
    """没有实义词时：只按地点/行业列出，精选与国家级优先。"""
    loc, loc_args = _location_clause(plan)
    ind = ""
    ind_args: list = []
    if plan.industries:
        ind = " AND (" + " OR ".join("p.industries LIKE ?" for _ in plan.industries) + ")"
        ind_args = [f'%"{i}"%' for i in plan.industries]
    rows = conn.execute(
        f"SELECT p.* FROM portals p WHERE 1=1{where}{loc}{ind}"
        " ORDER BY p.featured DESC, p.is_national DESC, p.has_api DESC, p.name LIMIT ?",
        [*args, *loc_args, *ind_args, limit],
    ).fetchall()
    return [PortalHit(r, 0.0, 1.0, [], {"按条件列出": 1.0}) for r in rows]


def _candidates(
    conn: sqlite3.Connection, plan: QueryPlan, match: str, where: str, args: list
) -> dict[str, sqlite3.Row]:
    base = (
        f"SELECT p.*, bm25(portals_fts, {_WEIGHTS}) AS bm FROM portals_fts"
        " JOIN portals p ON p.rowid = portals_fts.rowid"
        f" WHERE portals_fts MATCH ?{where}"
    )
    found: dict[str, sqlite3.Row] = {}

    def add(sql: str, params: list) -> None:
        for row in conn.execute(sql, params).fetchall():
            found.setdefault(row["id"], row)

    add(f"{base} ORDER BY bm LIMIT {_CANDIDATES}", [match, *args])
    loc, loc_args = _location_clause(plan)
    if loc:
        add(f"{base}{loc} ORDER BY bm LIMIT {_CANDIDATES}", [match, *args, *loc_args])
    add(f"{base} AND p.featured = 1 ORDER BY bm LIMIT 100", [match, *args])
    return found


def _industry_only_candidates(
    conn: sqlite3.Connection, plan: QueryPlan, where: str, args: list, found: dict
) -> None:
    """文本没命中、但行业标签与查询行业重合的精选门户，也放进候选。"""
    if not plan.industries:
        return
    ind = " OR ".join("p.industries LIKE ?" for _ in plan.industries)
    rows = conn.execute(
        f"SELECT p.*, 0.0 AS bm FROM portals p WHERE p.featured = 1 AND ({ind}){where}",
        [*[f'%"{i}"%' for i in plan.industries], *args],
    ).fetchall()
    for row in rows:
        found.setdefault(row["id"], row)


def search_portals(
    conn: sqlite3.Connection,
    query: str = "",
    *,
    country: str | None = None,
    type_: str | None = None,
    industry: str | None = None,
    api_only: bool = False,
    limit: int = 15,
) -> tuple[QueryPlan, list[PortalHit]]:
    plan = parse(query)
    where, args = _filters(country, type_, industry, api_only)
    match = plan.fts_match()
    if match is None:
        return plan, _list_by_conditions(conn, plan, where, args, limit)

    found = _candidates(conn, plan, match, where, args)
    _industry_only_candidates(conn, plan, where, args, found)

    wanted = set(plan.industries)
    hits: list[PortalHit] = []
    n_concepts = len(plan.concepts)
    for row in found.values():
        mine = set(json.loads(row["industries"]))
        padded = f" {row['tokens']} "
        matched: list[str] = []
        credit = 0.0
        for c in plan.concepts:
            if any(_alt_in(a, padded) for a in c.alts):
                matched.append(c.label)
                credit += 1.0  # 字面命中
            elif set(c.industries) & mine:
                matched.append(c.label)
                credit += 0.5  # 仅靠行业标签推断命中，权重减半
        if not matched:
            continue
        coverage = credit / n_concepts
        overlap = len(wanted & mine)
        boosts = _boosts(plan, row)
        score = (abs(row["bm"]) + _INDUSTRY_POINTS * overlap) * (0.25 + 0.75 * coverage)
        for factor in boosts.values():
            score *= factor
        explain = {"覆盖度": coverage, **boosts}
        if overlap:
            explain["行业重合"] = float(overlap)
        hits.append(PortalHit(row, score, coverage, matched, explain))
    hits.sort(key=lambda h: -h.score)
    return plan, hits[:limit]
