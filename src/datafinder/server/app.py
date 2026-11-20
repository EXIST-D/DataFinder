"""网页后端：一个静态单页 + 4 个接口。

- GET /api/status    门户库状态、版本
- GET /api/portals   门户检索（毫秒级，先出）
- GET /api/search    数据集检索，SSE 逐批推送"当前最佳结果快照"
- GET /api/inspect   按需读取某个数据集页面公布的文件列表（只允许本次会话搜到过的链接）

安全底线（本地工具，只留必要的）：
- Host 头必须是 localhost/127.0.0.1/[::1]，防 DNS 重绑定；
- inspect 只接受本服务搜索结果里出现过的 URL，不接受任意 URL；
- 严格 CSP：只加载本站资源。
"""

from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..engine.errors import SourceError
from ..finder import SearchResult, stream_search
from ..models import Dataset
from ..portals.search import search_portals
from ..portals.tag import industry_zh, load_config
from ..query.plan import country_zh_name
from ..sources.dataseek import find_executable, inspect_url
from ..sources.registry import load_specs
from ..store import connect, get_meta, portal_count

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
MAX_QUERY = 200
SEEN_LIMIT = 4000

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def _host_ok(request: Request) -> bool:
    host = (request.headers.get("host") or "").strip().lower()
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return name in ALLOWED_HOSTS


def _dataset_json(d: Dataset, titles: dict[str, str], explain: bool) -> dict[str, Any]:
    out = d.to_dict()
    out["where"] = [titles.get(s, s) for s in d.sources] or [titles.get(d.source, d.source)]
    out["score"] = round(d.score, 6)
    if not explain:
        out.pop("explain", None)
    return out


def _snapshot_json(snap: SearchResult, specs: dict, explain: bool) -> dict[str, Any]:
    titles = {sid: s.title for sid, s in specs.items()}
    plan = snap.plan
    return {
        "type": "snapshot",
        "final": snap.final,
        "complete": snap.complete,
        "candidates": snap.candidates,
        "plan": {
            "concepts": [c.label for c in plan.concepts],
            "query_en": plan.query_en(),
            "zh_terms": plan.zh_terms(),
            "location": plan.location_zh,
            "formats": plan.formats,
            "years": plan.years,
        },
        "datasets": [_dataset_json(d, titles, explain) for d in snap.datasets],
        "sources": [
            {
                "id": st.id,
                "title": st.title,
                "status": st.status,
                "count": st.count,
                "elapsed_ms": st.elapsed_ms,
                "message": st.message,
                "experimental": specs[st.id].status == "experimental",
            }
            for st in snap.statuses
        ],
    }


def _portal_json(hit) -> dict[str, Any]:
    row: sqlite3.Row = hit.row
    cfg = load_config()
    country = row["country"]
    ctype = cfg.catalog_types.get(row["catalog_type"] or "")
    text = (row["desc_zh"] or row["description"] or "").replace("\n", " ").strip()
    return {
        "name": row["name_zh"] or row["name"],
        "name_en": row["name"] if row["name_zh"] and row["name_zh"] != row["name"] else None,
        "url": row["url"],
        "country": (country_zh_name(country) or row["country_en"] or country) if country else None,
        "region": row["region_zh"],
        "type": ctype["zh"] if ctype else row["catalog_type"],
        "industries": [industry_zh(i) for i in hit.industries],
        "api": bool(row["has_api"]),
        "featured": bool(row["featured"]),
        "national": bool(row["is_national"]),
        "description": text[:140] + ("…" if len(text) > 140 else "") if text else None,
    }


def create_app() -> FastAPI:
    app = FastAPI(title="DataFinder", docs_url=None, redoc_url=None, openapi_url=None)
    specs = {s.id: s for s in load_specs()}
    seen_urls: OrderedDict[str, None] = OrderedDict()

    def remember(urls: list[str]) -> None:
        for u in urls:
            seen_urls[u] = None
            seen_urls.move_to_end(u)
        while len(seen_urls) > SEEN_LIMIT:
            seen_urls.popitem(last=False)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not _host_ok(request):
            return JSONResponse({"error": "不允许的 Host"}, status_code=400)
        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api") else "no-cache"
        )
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        conn = connect()
        try:
            count = portal_count(conn)
            synced = get_meta(conn, "synced_at")
        finally:
            conn.close()
        return {
            "version": __version__,
            "portals": count,
            "synced_at": synced,
            "dataseek": find_executable() is not None,
            "sources": [{"id": s.id, "title": s.title, "status": s.status} for s in specs.values()],
        }

    @app.get("/api/portals")
    def portals(
        q: str = Query("", max_length=MAX_QUERY),
        limit: int = Query(8, ge=1, le=30),
        country: str | None = Query(None, max_length=8),
    ) -> dict[str, Any]:
        conn = connect()
        try:
            if portal_count(conn) == 0:
                return {"synced": False, "portals": []}
            _, hits = search_portals(conn, q, country=country, limit=limit)
            return {"synced": True, "portals": [_portal_json(h) for h in hits]}
        finally:
            conn.close()

    @app.get("/api/search")
    async def search(
        request: Request,
        q: str = Query(..., min_length=1, max_length=MAX_QUERY),
        limit: int = Query(15, ge=1, le=30),
        sources: str | None = Query(None, max_length=200),
        refresh: bool = False,
        explain: bool = False,
    ) -> StreamingResponse:
        only = {x.strip() for x in sources.split(",") if x.strip()} if sources else None
        if not q.strip():
            raise HTTPException(400, "查询不能为空")
        unknown = (only or set()) - set(specs)
        if unknown:
            raise HTTPException(400, f"未知来源：{', '.join(sorted(unknown))}")

        async def events() -> AsyncIterator[str]:
            try:
                async for snap in stream_search(q, limit=limit, only=only, refresh=refresh):
                    remember([d.url for d in snap.datasets])
                    payload = _snapshot_json(snap, specs, explain)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    if await request.is_disconnected():
                        return
            except Exception as exc:  # 流里出错也要告诉前端，而不是静默断开
                err = {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.get("/api/inspect")
    async def inspect(url: str = Query(..., max_length=2000)) -> dict[str, Any]:
        if url not in seen_urls:
            raise HTTPException(400, "只能查看本次搜索结果里出现过的数据集")
        try:
            resources = await inspect_url(url)
        except SourceError as exc:
            raise HTTPException(502, str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(504, "读取文件列表超时") from exc
        return {"resources": [r.__dict__ for r in resources]}

    return app
