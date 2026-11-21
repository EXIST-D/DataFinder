"""网页后端：一个静态单页 + 一组接口。

搜索：
- GET /api/portals            门户检索（毫秒级，先出）
- GET /api/search             数据集检索，SSE 逐批推送"当前最佳结果快照"（含详情增强字段）
- GET /api/inspect            按需读取某个数据集页面公布的文件列表（只允许本次会话搜到过的链接）
资源目录（统一检索：主题 × 地域 × 资源类型）：
- GET /api/catalog/search|facets|inspect   目录检索、合法分面词表、资源详情（不联网；网页只展示获取方式，不执行下载）
- GET /api/catalog/tree       目录树：当前检索的命中集合按 主题/地域/来源 逐级展开（无状态，计数口径同 search 的 total）
门户目录：
- GET /api/browse/facets      各维度取值与条数（在其他筛选条件下）
- GET /api/browse/portals     分页门户列表
- GET /api/links              外部检索入口（带关键词）
领域接口：各领域包通过 routers 注册自己的网页接口（如统计数据库的逐级浏览），核心不 import 领域代码。

安全底线（本地工具，只留必要的）：
- Host 头必须是 localhost/127.0.0.1/[::1]，防 DNS 重绑定；
- inspect 只接受本服务搜索结果里出现过的 URL；
- 严格 CSP：只加载本站资源。
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, service
from ..engine.errors import SourceError
from ..enrich import enrich, lookup_portal
from ..finder import SearchResult, stream_search
from ..links import build_links
from ..models import Dataset
from ..portals.browse import Filters, browse, facets, portal_dict
from ..portals.search import search_portals
from ..sources.base import SourceSpec
from ..sources.dataseek import find_executable, inspect_url
from ..sources.registry import load_specs
from ..store import connect, get_meta, portal_count
from . import routers

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


def _dataset_json(
    d: Dataset,
    titles: dict[str, str],
    specs: dict[str, SourceSpec],
    portals: dict[str, dict | None],
    conn,
    explain: bool,
) -> dict[str, Any]:
    out = d.to_dict()
    out["where"] = [titles.get(s, s) for s in d.sources] or [titles.get(d.source, d.source)]
    out["score"] = round(d.score, 6)
    if not explain:
        out.pop("explain", None)
    host = d.url.split("/")[2] if "://" in d.url else d.url
    if host not in portals:  # 同一个主机只查一次
        portals[host] = lookup_portal(conn, d.url)
    e = enrich(d, spec=specs.get(d.source), spec_titles=titles, portal=portals[host])
    out.update(
        region=e.region,
        region_basis=e.region_basis,
        highlights=e.highlights,
        portal=e.portal,
        doi_url=e.doi_url,
        open_url=e.open_url,
    )
    return out


def _snapshot_json(snap: SearchResult, specs: dict[str, SourceSpec], explain: bool, conn) -> dict:
    titles = {sid: s.title for sid, s in specs.items()}
    plan = snap.plan
    portals: dict[str, dict | None] = {}
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
        "datasets": [
            _dataset_json(d, titles, specs, portals, conn, explain) for d in snap.datasets
        ],
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


def filter_params(
    domain: str | None = Query(None, max_length=32),
    country: str | None = Query(None, max_length=16),
    region: str | None = Query(None, max_length=16),
    tiers: str | None = Query(None, max_length=32),
    kind: str | None = Query(None, max_length=32),
    openness: str | None = Query(None, max_length=16),
    api: bool = False,
    official: bool = False,
    label: str | None = Query(None, max_length=32),
) -> Filters:
    try:
        tier_list = [int(x) for x in tiers.split(",") if x] if tiers else []
    except ValueError as exc:
        raise HTTPException(400, "tiers 必须是逗号分隔的数字") from exc
    return Filters(
        domain=domain or None,
        country=country or None,
        region=region or None,
        tiers=[t for t in tier_list if 1 <= t <= 6],
        kind=kind or None,
        openness=openness or None,
        api=api,
        official=official,
        label=label or None,
    )


_HTTP_STATUS = {
    "INVALID_FACET_ID": 400,
    "INVALID_ARGUMENT": 400,
    "NOT_FOUND": 404,
    "INDEX_NOT_READY": 503,
    "SOURCE_RATE_LIMITED": 429,
    "SOURCE_ACCESS_BLOCKED": 502,
    "SOURCE_TIMEOUT": 504,
    "UNSUPPORTED": 400,
}


def _service_http_error(exc: Exception) -> HTTPException:
    err = service.translate(exc)
    tail = f"（{err.next_action}）" if err.next_action else ""
    return HTTPException(_HTTP_STATUS.get(err.code, 502), f"[{err.code}] {err}{tail}")


def create_app() -> FastAPI:
    app = FastAPI(title="DataFinder", docs_url=None, redoc_url=None, openapi_url=None)
    specs = {s.id: s for s in load_specs()}
    seen_urls: OrderedDict[str, None] = OrderedDict()
    for r in routers.build_all():  # 领域包注册的网页接口（如统计数据库浏览）
        app.include_router(r)

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

    # ───────────── 状态 / 门户检索 ─────────────

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
            return {"synced": True, "portals": [portal_dict(h.row) for h in hits]}
        finally:
            conn.close()

    # ───────────── 分类浏览 ─────────────

    @app.get("/api/browse/facets")
    def browse_facets(f: Filters = Depends(filter_params)) -> dict[str, Any]:
        conn = connect()
        try:
            if portal_count(conn) == 0:
                return {"synced": False}
            return {"synced": True, **facets(conn, f)}
        finally:
            conn.close()

    @app.get("/api/browse/portals")
    def browse_portals(
        f: Filters = Depends(filter_params),
        sort: str = Query("official", pattern="^(official|name)$"),
        page: int = Query(1, ge=1, le=10000),
        size: int = Query(20, ge=1, le=50),
    ) -> dict[str, Any]:
        conn = connect()
        try:
            if portal_count(conn) == 0:
                return {"synced": False, "total": 0, "portals": []}
            rows, total = browse(conn, f, sort=sort, page=page, size=size)
            return {
                "synced": True,
                "total": total,
                "page": page,
                "size": size,
                "portals": [portal_dict(r) for r in rows],
            }
        finally:
            conn.close()

    @app.get("/api/links")
    def links(q: str = Query("", max_length=MAX_QUERY)) -> dict[str, Any]:
        return {"query": q, "groups": build_links(q)}

    # ───────────── 资源目录（统一检索：主题 × 地域 × 资源类型）─────────────

    def _csv(value: str) -> list[str]:
        return [x.strip() for x in value.split(",") if x.strip()][:10]

    @app.get("/api/catalog/search")
    async def catalog_search(
        q: str = Query("", max_length=MAX_QUERY),
        topic: str = Query("", max_length=200),
        region: str = Query("", max_length=200),
        kind: str = Query("", max_length=200),
        delivery: str = Query("", max_length=60),
        granularity: str | None = Query(None, max_length=20),
        mode: str = Query("discovery", max_length=12),
        y1: int | None = Query(None, ge=1000, le=3000),
        y2: int | None = Query(None, ge=1000, le=3000),
        domain: str = Query("", max_length=200),
        source: str = Query("", max_length=200),
        limit: int = Query(20, ge=1, le=50),
        offset: int = Query(0, ge=0, le=5000),
    ) -> dict[str, Any]:
        try:
            return await service.search(
                q,
                domain_ids=_csv(domain),
                source_ids=_csv(source),
                topic_ids=_csv(topic),
                region_ids=_csv(region),
                kinds_=_csv(kind),
                delivery=_csv(delivery),
                geo_granularity=granularity,
                mode=mode,
                period_start=y1,
                period_end=y2,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:  # noqa: BLE001 —— 边界层：统一翻译成 HTTP 错误
            raise _service_http_error(exc) from exc

    @app.get("/api/catalog/tree")
    def catalog_tree(
        axis: str = Query("", max_length=12),
        node: str = Query("", max_length=400),
        q: str = Query("", max_length=MAX_QUERY),
        topic: str = Query("", max_length=200),
        region: str = Query("", max_length=200),
        kind: str = Query("", max_length=200),
        delivery: str = Query("", max_length=60),
        granularity: str | None = Query(None, max_length=20),
        mode: str = Query("discovery", max_length=12),
        y1: int | None = Query(None, ge=1000, le=3000),
        y2: int | None = Query(None, ge=1000, le=3000),
        domain: str = Query("", max_length=200),
        source: str = Query("", max_length=200),
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0, le=5000),
    ) -> dict[str, Any]:
        try:
            return service.tree(
                axis,
                node,
                q,
                domain_ids=_csv(domain),
                source_ids=_csv(source),
                topic_ids=_csv(topic),
                region_ids=_csv(region),
                kinds_=_csv(kind),
                delivery=_csv(delivery),
                geo_granularity=granularity,
                mode=mode,
                period_start=y1,
                period_end=y2,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:  # noqa: BLE001
            raise _service_http_error(exc) from exc

    @app.get("/api/catalog/facets")
    def catalog_facets() -> dict[str, Any]:
        try:
            return service.facets()
        except Exception as exc:  # noqa: BLE001
            raise _service_http_error(exc) from exc

    @app.get("/api/catalog/inspect")
    def catalog_inspect(
        id: str = Query(..., max_length=400),
        children: bool = False,
        offset: int = Query(0, ge=0, le=5000),
    ) -> dict[str, Any]:  # noqa: A002
        try:
            return service.inspect(id, children=children, limit=30, offset=offset)
        except Exception as exc:  # noqa: BLE001
            raise _service_http_error(exc) from exc

    # ───────────── 数据集搜索（SSE）与 inspect ─────────────

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
            conn = connect()
            try:
                async for snap in stream_search(q, limit=limit, only=only, refresh=refresh):
                    remember([d.url for d in snap.datasets])
                    payload = _snapshot_json(snap, specs, explain, conn)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    if await request.is_disconnected():
                        return
            except Exception as exc:  # 流里出错也要告诉前端，而不是静默断开
                err = {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            finally:
                conn.close()

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
