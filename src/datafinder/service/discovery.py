"""门户目录浏览与跨来源数据集检索。"""

from __future__ import annotations

from ..enrich import enrich, lookup_portal
from ..finder import search_datasets
from ..portals.browse import Filters, browse, portal_dict
from ..sources.registry import load_specs
from ..store import connect, portal_count
from .errors import ServiceError

# ───────────── 门户与数据集 ─────────────


def portals_list(
    *,
    domain: str | None = None,
    country: str | None = None,
    region: str | None = None,
    official: bool = False,
    label: str | None = None,
    api: bool = False,
    page: int = 1,
    size: int = 15,
) -> dict:
    conn = connect()
    try:
        if not portal_count(conn):
            raise ServiceError("门户库为空，请先在终端运行 datafinder sync")
        rows, total = browse(
            conn,
            Filters(domain=domain, country=country, region=region, official=official, label=label, api=api),
            page=page,
            size=size,
        )
        return {"total": total, "page": page, "portals": [portal_dict(r) for r in rows]}
    finally:
        conn.close()


async def datasets_search(query: str, limit: int = 10) -> dict:
    """跨来源检索数据集（国家统计局、Zenodo、DataCite、HDX、各国开放数据等）。"""
    snap = await search_datasets(query, limit=max(1, min(limit, 30)))
    specs = {s.id: s for s in load_specs()}
    titles = {sid: s.title for sid, s in specs.items()}
    conn = connect()
    try:
        out = []
        for d in snap.datasets:
            e = enrich(d, spec=specs.get(d.source), spec_titles=titles, portal=lookup_portal(conn, d.url))
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
        "query": query,
        "datasets": out,
        "sources": [
            {"id": s.id, "title": s.title, "status": s.status, "count": s.count, "message": s.message}
            for s in snap.statuses
        ],
        "note": "来源失败不等于没有数据：请看 sources 里每个来源的 status。",
    }
