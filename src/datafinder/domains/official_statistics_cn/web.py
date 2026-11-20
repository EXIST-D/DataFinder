"""本领域的网页接口：国家统计局分类树逐级浏览、取值，省级统计局与省市开放平台总览。

挂在核心网页服务上（通过 plugin 注册的路由工厂），核心代码不 import 本模块。
安全：数据库代码只接受已登记的库；各种 id 只接受 32 位十六进制 / 12 位数字；访问防护触发时返回 429。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from datafinder.engine.cache import Cache
from datafinder.engine.errors import SourceError
from datafinder.engine.http import Http
from datafinder.finder import MIN_INTERVAL
from datafinder.portals.browse import Filters, browse, portal_dict
from datafinder.store import connect, portal_count

from .nbs.client import DATABASES, MAX_AREAS, NbsClient, valid_area, valid_id

router = APIRouter()


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



@router.get("/api/china/overview")
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

@router.get("/api/nbs/tree")
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

@router.get("/api/nbs/indicators")
async def nbs_indicators(cid: str = Query(..., max_length=32)) -> dict[str, Any]:
    check_id(cid, "cid")
    try:
        async with nbs() as client:
            return {"cid": cid, "indicators": await client.indicators(cid)}
    except (SourceError, TimeoutError) as exc:
        raise _nbs_http_error(exc) from exc

@router.get("/api/nbs/regions")
async def nbs_regions(cid: str = Query(..., max_length=32)) -> dict[str, Any]:
    check_id(cid, "cid")
    try:
        async with nbs() as client:
            return {"cid": cid, **await client.regions(cid)}
    except (SourceError, TimeoutError) as exc:
        raise _nbs_http_error(exc) from exc

@router.get("/api/nbs/series")
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
