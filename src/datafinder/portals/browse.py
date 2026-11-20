"""分类浏览：多维筛选 + 分面计数（facet counts）。

每个维度的"条数"是在**其他已选条件**下的条数（不含它自己），这样用户点之前就知道能点出多少，
也不会因为已选了某个值而让同维度其他值全部变成 0。
维度见 docs/03_分类与信息架构方案.md：领域、国家/地区(+省)、机构类型、数据形态、开放程度、API、专区标签。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, replace

from ..query.plan import country_zh_name as _country_zh_name
from ..query.plan import province_zh_name
from .facets import is_official, load_facets, tier_zh
from .tag import industry_zh, load_config

PAGE_SIZE_MAX = 50
_NON_COUNTRY = {"World": "全球/国际"}


def country_zh_name(code: str) -> str | None:
    return _NON_COUNTRY.get(code) or _country_zh_name(code)


@dataclass
class Filters:
    domain: str | None = None  # industries.yaml 的 id
    country: str | None = None  # ISO 国家码，或 "World"
    region: str | None = None  # 省级码，如 CN-GD
    tiers: list[int] = field(default_factory=list)  # 机构类型档位 1–6
    kind: str | None = None
    openness: str | None = None
    api: bool = False
    official: bool = False  # 仅官方（档位 1–3）
    label: str | None = None  # 专区标签：stats_bureau / open_platform


_DEFAULTS = Filters()


def _without(f: Filters, *names: str) -> Filters:
    """复制一份筛选条件，把指定维度重置为默认值（算该维度自己的条数用）。"""
    return replace(f, **{n: getattr(_DEFAULTS, n) for n in names})


def _clauses(f: Filters) -> tuple[list[str], list]:
    where: list[str] = []
    args: list = []
    if f.domain:
        where.append(
            "EXISTS (SELECT 1 FROM portal_industries pi"
            " WHERE pi.portal_rowid = p.rowid AND pi.industry = ?)"
        )
        args.append(f.domain)
    if f.country:
        where.append("p.country = ?")
        args.append(f.country)
    if f.region:
        where.append("p.region = ?")
        args.append(f.region)
    if f.tiers:
        where.append(f"p.tier IN ({','.join('?' * len(f.tiers))})")
        args += f.tiers
    if f.official:
        where.append("p.tier IN (1, 2, 3)")
    if f.kind:
        where.append("p.kind = ?")
        args.append(f.kind)
    if f.openness:
        where.append("p.openness = ?")
        args.append(f.openness)
    if f.api:
        where.append("p.has_api = 1")
    if f.label:
        where.append("p.labels LIKE ?")
        args.append(f'%"{f.label}"%')
    return where, args


def _where_sql(f: Filters) -> tuple[str, list]:
    where, args = _clauses(f)
    return ("WHERE " + " AND ".join(where)) if where else "", args


_ORDER = {
    # 官方优先：精选 → 国家级 → 机构类型档位（官方在前）→ 有 API → 名称
    "official": "p.featured DESC, p.is_national DESC, COALESCE(p.tier, 9) ASC, p.has_api DESC,"
    " COALESCE(p.name_zh, p.name) COLLATE NOCASE",
    "name": "COALESCE(p.name_zh, p.name) COLLATE NOCASE",
}


def browse(
    conn: sqlite3.Connection, f: Filters, *, sort: str = "official", page: int = 1, size: int = 20
) -> tuple[list[sqlite3.Row], int]:
    size = max(1, min(size, PAGE_SIZE_MAX))
    page = max(1, page)
    where, args = _where_sql(f)
    total = conn.execute(f"SELECT COUNT(*) FROM portals p {where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT p.* FROM portals p {where} ORDER BY {_ORDER.get(sort, _ORDER['official'])}"
        " LIMIT ? OFFSET ?",
        [*args, size, (page - 1) * size],
    ).fetchall()
    return rows, total


def _count_by(conn: sqlite3.Connection, column: str, f: Filters) -> dict:
    where, args = _where_sql(f)
    sql = f"SELECT p.{column} AS v, COUNT(*) AS n FROM portals p {where} GROUP BY p.{column}"
    return {r["v"]: r["n"] for r in conn.execute(sql, args)}


def _count(conn: sqlite3.Connection, f: Filters, extra: str = "") -> int:
    where, args = _clauses(f)
    if extra:
        where.append(extra)
    sql = "SELECT COUNT(*) FROM portals p" + (" WHERE " + " AND ".join(where) if where else "")
    return conn.execute(sql, args).fetchone()[0]


def facets(conn: sqlite3.Connection, f: Filters) -> dict:
    cfg = load_config()
    fx = load_facets()

    # 领域：经 portal_industries 计数（一个门户可属多个领域），不应用领域自己的筛选
    where, args = _clauses(_without(f, "domain"))
    sql = (
        "SELECT pi.industry AS industry, COUNT(*) AS n FROM portal_industries pi"
        " JOIN portals p ON p.rowid = pi.portal_rowid"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " GROUP BY pi.industry"
    )
    dom_counts = {r["industry"]: r["n"] for r in conn.execute(sql, args)}
    domains = [
        {"id": i.id, "zh": i.zh, "en": i.en, "count": dom_counts.get(i.id, 0)}
        for i in cfg.industries.values()
    ]

    # 国家：不应用国家与省份筛选（选了省份后其他国家仍应显示条数，便于切换）
    countries = sorted(
        (
            {"id": c, "zh": country_zh_name(c) or c, "count": n}
            for c, n in _count_by(conn, "country", _without(f, "country", "region")).items()
            if c
        ),
        key=lambda x: -x["count"],
    )

    # 省份：选了国家才给（目前只有中国有省级数据）
    regions: list[dict] = []
    if f.country:
        regions = sorted(
            (
                {"id": r, "zh": province_zh_name(r) or r, "count": n}
                for r, n in _count_by(conn, "region", _without(f, "region")).items()
                if r
            ),
            key=lambda x: -x["count"],
        )

    tier_counts = _count_by(conn, "tier", _without(f, "tiers"))
    kind_counts = _count_by(conn, "kind", _without(f, "kind"))
    open_counts = _count_by(conn, "openness", _without(f, "openness"))
    return {
        "total": _count(conn, f),
        "domains": domains,
        "countries": countries,
        "regions": regions,
        "tiers": [
            {"id": t.tier, "zh": t.zh, "official": t.official, "count": tier_counts.get(t.tier, 0)}
            for t in fx.tiers
        ],
        "kinds": [{"id": k.key, "zh": k.zh, "count": kind_counts.get(k.key, 0)} for k in fx.kinds],
        "openness": [{"id": k, "zh": zh, "count": open_counts.get(k, 0)} for k, zh in fx.openness],
        "api": _count(conn, _without(f, "api"), "p.has_api = 1"),
        "official": _count(conn, _without(f, "official"), "p.tier IN (1, 2, 3)"),
    }


def portal_dict(row: sqlite3.Row) -> dict:
    """门户行 → 前端展示用的 JSON（含机构类型、数据形态、开放程度、备注、核验状态）。"""
    fx = load_facets()
    kinds = {k.key: k.zh for k in fx.kinds}
    opens = dict(fx.openness)
    cfg = load_config()
    country = row["country"]
    ctype = cfg.catalog_types.get(row["catalog_type"] or "")
    text = (row["desc_zh"] or row["description"] or "").replace("\n", " ").strip()
    return {
        "id": row["id"],
        "name": row["name_zh"] or row["name"],
        "name_en": row["name"] if row["name_zh"] and row["name_zh"] != row["name"] else None,
        "url": row["url"],
        "country": (country_zh_name(country) or row["country_en"] or country) if country else None,
        "country_code": country,
        "region": row["region_zh"],
        "region_code": row["region"],
        "type": ctype["zh"] if ctype else row["catalog_type"],
        "kind": kinds.get(row["kind"]),
        "tier": row["tier"],
        "org_type": tier_zh(row["tier"]),
        "official": is_official(row["tier"]),
        "openness": opens.get(row["openness"]),
        "industries": [industry_zh(i) for i in json.loads(row["industries"])],
        "api": bool(row["has_api"]),
        "featured": bool(row["featured"]),
        "national": bool(row["is_national"]),
        "note": row["note"],
        "verify": row["verify"],
        "description": (text[:140] + ("…" if len(text) > 140 else "")) if text else None,
    }
