"""网页后端：一个静态单页 + 一组接口。

搜索：
- GET /api/portals            门户检索（毫秒级，先出）
- GET /api/search             数据集检索，SSE 逐批推送"当前最佳结果快照"（含详情增强字段）
- GET /api/inspect            按需读取某个数据集页面公布的文件列表（只允许本次会话搜到过的链接）
分类浏览：
- GET /api/browse/facets      各维度取值与条数（在其他筛选条件下）
- GET /api/browse/portals     分页门户列表
- GET /api/links              外部检索入口（带关键词）
中国统计：
- GET /api/china/overview     省级统计局、省市开放平台、国家统计局数据库列表
- GET /api/nbs/tree|indicators|regions|series   国家统计局分类树、指标、地区、取值

安全底线（本地工具，只留必要的）：
- Host 头必须是 localhost/127.0.0.1/[::1]，防 DNS 重绑定；
- inspect 只接受本服务搜索结果里出现过的 URL；国家统计局的各种 id 只接受 32 位十六进制 / 12 位数字；
- 严格 CSP：只加载本站资源。
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..domains.official_statistics_cn.nbs.client import (
    DATABASES,
    MAX_AREAS,
    NbsClient,
    valid_area,
    valid_id,
)
from ..engine.cache import Cache
from ..engine.errors import SourceError
from ..engine.http import Http
from ..enrich import enrich, lookup_portal
from ..finder import MIN_INTERVAL, SearchResult, stream_search
from ..links import build_links
from ..models import Dataset
from ..portals.browse import Filters, browse, facets, portal_dict
from ..portals.search import search_portals
from ..sources.base import SourceSpec
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


def _nbs_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SourceError):
        status = 429 if exc.kind in ("rate_limited", "blocked") else 502
        return HTTPException(status, str(exc))
    if isinstance(exc, TimeoutError):
        return HTTPException(504, "国家统计局响应超时")
    return HTTPException(502, f"{type(exc).__name__}: {exc}")


# 行政区划通行顺序（北京、天津、河北……），比按拼音或区划代码更符合阅读习惯
_ADMIN_ORDER = {
    f"CN-{c}": i
    for i, c in enumerate(
        "BJ TJ HE SX NM LN JL HL SH JS ZJ AH FJ JX SD HA HB HN GD GX HI CQ SC GZ YN XZ SN GS QH NX XJ".split()
    )
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

    # ───────────── 中国统计 ─────────────

    @app.get("/api/china/overview")
    def china_overview() -> dict[str, Any]:
        conn = connect()
        try:

            def by_label(label: str) -> list[dict]:
                rows, _ = browse(
                    conn, Filters(country="CN", label=label), sort="name", page=1, size=50
                )
                return [portal_dict(r) for r in rows]

            bureaus = by_label("stats_bureau")
            bureaus.sort(key=lambda p: _ADMIN_ORDER.get(p["region_code"] or "", 99))
            return {
                "synced": portal_count(conn) > 0,
                "databases": [
                    {"code": code, "name": name, "dt_type": dt}
                    for code, (name, dt) in DATABASES.items()
                ],
                "bureaus": bureaus,
                "platforms": by_label("open_platform"),
            }
        finally:
            conn.close()

    @asynccontextmanager
    async def nbs() -> AsyncIterator[NbsClient]:
        http = Http(min_interval=MIN_INTERVAL)
        cache = Cache()
        try:
            yield NbsClient(http, cache)
        finally:
            await http.aclose()
            cache.close()

    def check_id(value: str, what: str) -> str:
        if not valid_id(value):
            raise HTTPException(400, f"{what} 格式不正确")
        return value

    def check_code(code: str) -> str:
        if code not in DATABASES:
            raise HTTPException(400, f"未知数据库代码：{code}")
        return code

    @app.get("/api/nbs/tree")
    async def nbs_tree(
        code: str = Query(..., max_length=3), pid: str = Query("", max_length=32)
    ) -> dict[str, Any]:
        check_code(code)
        if pid:
            check_id(pid, "pid")
        try:
            async with nbs() as client:
                return {"code": code, "pid": pid, "nodes": await client.tree(code, pid)}
        except (SourceError, TimeoutError) as exc:
            raise _nbs_http_error(exc) from exc

    @app.get("/api/nbs/indicators")
    async def nbs_indicators(cid: str = Query(..., max_length=32)) -> dict[str, Any]:
        check_id(cid, "cid")
        try:
            async with nbs() as client:
                return {"cid": cid, "indicators": await client.indicators(cid)}
        except (SourceError, TimeoutError) as exc:
            raise _nbs_http_error(exc) from exc

    @app.get("/api/nbs/regions")
    async def nbs_regions(cid: str = Query(..., max_length=32)) -> dict[str, Any]:
        check_id(cid, "cid")
        try:
            async with nbs() as client:
                return {"cid": cid, **await client.regions(cid)}
        except (SourceError, TimeoutError) as exc:
            raise _nbs_http_error(exc) from exc

    @app.get("/api/nbs/series")
    async def nbs_series(
        code: str = Query(..., max_length=3),
        cid: str = Query(..., max_length=32),
        ind: str = Query(..., max_length=32),
        root: str = Query(..., max_length=32),
        areas: str = Query(..., max_length=200),
        n: int = Query(8, ge=1, le=30),
    ) -> dict[str, Any]:
        check_code(code)
        for value, what in ((cid, "cid"), (ind, "ind"), (root, "root")):
            check_id(value, what)
        codes = [a for a in areas.split(",") if a]
        if not codes or len(codes) > MAX_AREAS or not all(valid_area(a) for a in codes):
            raise HTTPException(400, f"areas 必须是 1–{MAX_AREAS} 个 12 位地区代码")
        try:
            async with nbs() as client:
                known = {r["value"]: r["text"] for r in (await client.regions(cid))["regions"]}
                unknown = [c for c in codes if c not in known]
                if unknown:
                    raise HTTPException(400, f"该数据集不支持这些地区代码：{', '.join(unknown)}")
                return await client.series(
                    code=code,
                    cid=cid,
                    indicator_id=ind,
                    root_id=root,
                    areas=[{"text": known[c], "value": c} for c in codes],
                    n=n,
                )
        except (SourceError, TimeoutError) as exc:
            raise _nbs_http_error(exc) from exc

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
