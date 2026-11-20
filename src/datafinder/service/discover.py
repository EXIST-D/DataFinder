"""检索、分面词表、来源列表：目录（统一资源）+ 门户库 +（可选）实时外部来源。

目录检索永远不联网；`live=True` 才会向 Zenodo、DataCite、各国开放数据等实时来源发请求，
它们的结果与目录结果分开放（来源状态单独标注，失败不等于没有数据）。
"""

from __future__ import annotations

import json
from typing import Any

from datafinder.catalog import SearchRequest, facet_counts, kinds, regions, taxonomy
from datafinder.catalog import search as catalog_search
from datafinder.catalog.adapters import adapters
from datafinder.enrich import enrich, lookup_portal
from datafinder.finder import search_datasets
from datafinder.portals.browse import portal_dict
from datafinder.portals.search import search_portals
from datafinder.sources.registry import load_specs
from datafinder.store import connect, portal_count

from .common import open_catalog
from .errors import guard

PORTAL_MAX = 8


def _portals(query: str, req: SearchRequest, limit: int) -> list[dict[str, Any]]:
    """门户库命中（门户永远是"只有链接"）。点名了地区时，只保留全国性门户和该地区的门户。"""
    conn = connect()
    try:
        if not query.strip() or not portal_count(conn):
            return []
        _, hits = search_portals(conn, query, limit=limit * 3)
        items = [portal_dict(h.row) for h in hits]
    finally:
        conn.close()
    if req.region_ids:
        wanted = {*req.region_ids, *(a for r in req.region_ids for a in regions.ancestors(r))}
        items = [p for p in items if not p.get("region_code") or p["region_code"] in wanted]
    return [
        {
            "kind": "portal",
            "title": p["name"],
            "url": p["url"],
            "delivery": "link",
            "org_type": p["org_type"],
            "official": p["official"],
            "country": p["country"],
            "region": p["region"],
            "note": p["note"],
            "verify": p["verify"],
            "industries": p["industries"],
        }
        for p in items[:limit]
    ]


async def _live(query: str, limit: int) -> dict[str, Any]:
    snap = await search_datasets(query, limit=max(1, min(limit, 30)))
    specs = {s.id: s for s in load_specs()}
    titles = {sid: s.title for sid, s in specs.items()}
    conn = connect()
    try:
        out = []
        for d in snap.datasets:
            e = enrich(
                d, spec=specs.get(d.source), spec_titles=titles, portal=lookup_portal(conn, d.url)
            )
            item = d.to_dict()
            for k in ("explain", "score", "aliases"):
                item.pop(k, None)
            item.update(
                where=[titles.get(s, s) for s in d.sources] or [titles.get(d.source, d.source)],
                region=e.region,
                open_url=e.open_url,
                doi_url=e.doi_url,
                access=next((h["value"] for h in e.highlights if h["label"] == "获取方式"), None),
            )
            out.append(item)
    finally:
        conn.close()
    return {
        "datasets": out,
        "sources": [
            {
                "id": s.id,
                "title": s.title,
                "status": s.status,
                "count": s.count,
                "message": s.message,
            }
            for s in snap.statuses
        ],
        "note": "来源失败不等于没有数据：请看 sources 里每个来源的 status。",
    }


async def search(
    query: str = "",
    *,
    domain_ids: list[str] | None = None,
    topic_ids: list[str] | None = None,
    region_ids: list[str] | None = None,
    region_match: str = "intersects",
    geo_granularity: str | None = None,
    kinds_: list[str] | None = None,
    delivery: list[str] | None = None,
    period_start: int | None = None,
    period_end: int | None = None,
    frequency: str | None = None,
    source_ids: list[str] | None = None,
    mode: str = "discovery",
    limit: int = 20,
    offset: int = 0,
    facets: bool = True,
    live: bool = False,
) -> dict[str, Any]:
    with guard():
        req = SearchRequest(
            query=query,
            domain_ids=domain_ids or [],
            topic_ids=topic_ids or [],
            region_ids=region_ids or [],
            region_match=region_match,
            geo_granularity=geo_granularity,
            kinds=kinds_ or [],
            delivery=delivery or [],
            period_start=period_start,
            period_end=period_end,
            frequency=frequency,
            source_ids=source_ids or [],
            mode=mode,
            limit=limit,
            offset=offset,
            include_facets=facets,
        ).normalized()
        with open_catalog() as cat:
            out = catalog_search(cat, req).to_dict()
        want_portals = (not req.kinds or "portal" in req.kinds) and not (
            req.topic_ids or req.delivery or req.period_start or req.period_end
        )
        out["portals"] = _portals(query, req, PORTAL_MAX) if want_portals else []
        out["is_complete"] = True
        if live and query.strip():
            out["live"] = await _live(query, limit)
            out["is_complete"] = all(
                s["status"] in ("ok", "stale", "skipped") for s in out["live"]["sources"]
            )
        return out


def facets(domain_id: str | None = None) -> dict[str, Any]:
    """合法的主题树、地域、资源类型与获取方式（带当前目录里的计数），供检索时选用。"""
    with guard(), open_catalog() as cat:
        counts = facet_counts(cat, SearchRequest(domain_ids=[domain_id] if domain_id else []))
        topic_counts = {t["id"]: t["count"] for t in counts["topics"]}
        tree = [
            {
                "id": t.id,
                "name": t.name_zh,
                "level": t.level,
                "parent_id": t.parent_id,
                "aliases": list(t.aliases),
                "count": topic_counts.get(t.id, 0),
            }
            for t in taxonomy.topics().values()
            if not t.reserved and (not domain_id or t.domain == domain_id)
        ]
        return {
            "topics": tree,
            "regions": counts["regions"],
            "kinds": [
                {
                    "id": k,
                    "name": v["zh"],
                    "count": next((c["count"] for c in counts["kinds"] if c["id"] == k), 0),
                }
                for k, v in kinds.kinds().items()
                if not v.get("navigational")
            ],
            "delivery": counts["delivery"],
            "region_levels": ["country", "province", "prefecture", "county"],
            "modes": {
                "discovery": "高召回：信息未知的资源也返回并标注缺什么",
                "strict": "高精度：未知/未验证一律不算满足",
            },
        }


def sources() -> dict[str, Any]:
    """目录里的来源（含能力、规模、可访问状态）与实时检索来源、门户库规模。"""
    with guard(), open_catalog(require_data=False) as cat:
        info = cat.stats()
        adapter_caps = {a.id: sorted(a.capabilities) for a in adapters().values()}
        rows = cat.conn.execute("SELECT * FROM sources ORDER BY domain_id, source_id").fetchall()
        blocked = {
            r["source_id"]: (r["access_status"], r["extra"])
            for r in cat.conn.execute(
                "SELECT source_id, access_status, extra FROM resources WHERE kind='series' AND access_status != 'unknown'"
            )
        }
        catalog_sources = [
            {
                "source_id": r["source_id"],
                "name": r["name"],
                "domain_id": r["domain_id"],
                "adapter": r["adapter_id"],
                "capabilities": json.loads(r["capabilities"])
                or adapter_caps.get(r["adapter_id"], []),
                "resources": info["sources"].get(r["source_id"], {}).get("resources", 0),
                "access_status": blocked.get(r["source_id"], ("unknown", ""))[0],
            }
            for r in rows
        ]
    conn = connect()
    try:
        portals = portal_count(conn)
    finally:
        conn.close()
    return {
        "catalog_sources": catalog_sources,
        "live_sources": [
            {
                "id": s.id,
                "title": s.title,
                "protocol": s.protocol,
                "status": s.status,
                "official": s.official,
            }
            for s in load_specs()
        ],
        "portals": portals,
        "adapters": adapter_caps,
    }
