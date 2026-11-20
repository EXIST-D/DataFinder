"""国家统计局：浏览、找指标、取数（整洁长表）。"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from ..engine.cache import Cache
from ..engine.errors import SourceError
from ..engine.http import Http
from ..finder import MIN_INTERVAL
from ..nbs import index as nbs_index
from ..nbs.client import (
    DATABASES,
    MAX_AREAS_TABLE,
    NATIONAL,
    PAGE,
    NbsClient,
    clean_name,
    valid_area,
    valid_id,
)
from ..tables import Table, to_number
from .errors import ServiceError

# ───────────── 国家统计局 ─────────────


@asynccontextmanager
async def _nbs() -> AsyncIterator[NbsClient]:
    http = Http(min_interval=MIN_INTERVAL)
    cache = Cache()
    try:
        yield NbsClient(http, cache)
    finally:
        await http.aclose()
        cache.close()


def _wrap(exc: Exception) -> ServiceError:
    if isinstance(exc, SourceError) and exc.kind in ("rate_limited", "blocked"):
        return ServiceError(
            "国家统计局的访问防护要求验证，已暂停访问（不会重试或绕过）。请等几分钟再试。"
        )
    if isinstance(exc, TimeoutError):
        return ServiceError("国家统计局接口超时，请稍后再试。")
    return ServiceError(f"国家统计局接口出错：{exc}")


def nbs_databases() -> list[dict]:
    return [
        {"db": code, "name": name, "time_type": dt, "regional": code in nbs_index.REGIONAL_DBS}
        for code, (name, dt) in DATABASES.items()
    ]


def _check_db(db: str) -> str:
    if db not in DATABASES:
        raise ServiceError(f"未知数据库代码 {db!r}，可选：{', '.join(DATABASES)}（见 nbs_databases）")
    return db


def _check_id(value: str, what: str) -> str:
    if not valid_id(value):
        raise ServiceError(f"{what} 格式不对（应为 32 位十六进制 id，来自 nbs_browse / nbs_find 的结果）")
    return value


async def nbs_browse(db: str, node_id: str = "") -> dict:
    """逐级浏览分类树。叶子节点（is_leaf=true）的 id 就是 cid，用于 nbs_indicators / nbs_fetch。"""
    _check_db(db)
    if node_id:
        _check_id(node_id, "node_id")
    try:
        async with _nbs() as c:
            nodes = await c.tree(db, node_id)
            if not node_id and len(nodes) == 1 and not nodes[0]["is_leaf"]:
                nodes = await c.tree(db, nodes[0]["id"])  # 每个库只有一个根：直接给第一层
    except (SourceError, TimeoutError) as exc:
        raise _wrap(exc) from exc
    return {"db": db, "database": DATABASES[db][0], "nodes": nodes}


async def nbs_indicators(cid: str) -> dict:
    _check_id(cid, "cid")
    try:
        async with _nbs() as c:
            return {"cid": cid, "indicators": await c.indicators(cid), **await c.regions(cid)}
    except (SourceError, TimeoutError) as exc:
        raise _wrap(exc) from exc


async def nbs_search_national(keyword: str, limit: int = 12) -> list[dict]:
    """官方全局搜索（只有全国口径，匹配较模糊）。"""
    try:
        async with _nbs() as c:
            return await c.search_national(keyword.strip(), limit)
    except (SourceError, TimeoutError) as exc:
        raise _wrap(exc) from exc


def nbs_find(
    query: str, *, db: str | None = None, regional: bool | None = None, limit: int = 15
) -> dict:
    """按关键词在本地指标索引里找指标（需要先建索引）。"""
    conn = nbs_index.connect_index()
    try:
        info = nbs_index.stats(conn)
        if not info["indicators"]:
            raise ServiceError(
                "指标索引还没建立。请先在终端运行 `datafinder nbs index`（一次性，十几分钟，可中断续跑），"
                "或改用 nbs_browse 逐级浏览分类树。"
            )
        if db:
            _check_db(db)
        hits = nbs_index.search(conn, query, dbs=[db] if db else None, regional=regional, limit=limit)
    finally:
        conn.close()
    indexed = list(info["databases"])
    return {
        "query": query,
        "indexed_databases": indexed,
        "hits": hits,
        "hint": None if hits else f"没有命中。已索引的库：{', '.join(indexed)}；可换个说法，或 nbs_browse 浏览。",
    }


_REGION_SUFFIX = re.compile(r"(维吾尔自治区|壮族自治区|回族自治区|特别行政区|自治区|省|市)$")


def _norm_region(name: str) -> str:
    return _REGION_SUFFIX.sub("", name.strip())


def resolve_regions(available: list[dict], wanted: list[str]) -> list[dict]:
    """把"河北"、"河北省"、"130000000000"、"全部" 解析成该数据集支持的地区。"""
    by_code = {r["value"]: r for r in available}
    chosen: dict[str, dict] = {}
    for w in wanted:
        w = w.strip()
        if not w:
            continue
        if w in ("全部", "all", "*", "全部省份", "所有省份"):
            for r in available:
                if r["value"] != NATIONAL["value"]:
                    chosen[r["value"]] = r
            continue
        if valid_area(w):
            if w not in by_code:
                raise ServiceError(f"该数据集不支持地区代码 {w}")
            chosen[w] = by_code[w]
            continue
        key = _norm_region(w)
        match = [r for r in available if r["text"] == w] or [
            r for r in available if _norm_region(r["text"]) == key
        ]
        if not match:
            sample = "、".join(r["text"] for r in available[:12])
            raise ServiceError(f"该数据集没有地区「{w}」。可选地区示例：{sample}……")
        chosen[match[0]["value"]] = match[0]
    if not chosen:
        raise ServiceError("没有指定地区（regions 不能为空）")
    if len(chosen) > MAX_AREAS_TABLE:
        raise ServiceError(f"一次最多取 {MAX_AREAS_TABLE} 个地区")
    return list(chosen.values())


def _resolve_indicators(catalog: list[dict], wanted: list[str]) -> list[dict]:
    chosen: dict[str, dict] = {}
    by_id = {i["id"]: i for i in catalog}
    for w in wanted:
        w = w.strip()
        if valid_id(w):
            if w not in by_id:
                raise ServiceError(f"该数据集（cid）下没有指标 {w}，请用 nbs_indicators 查看清单")
            chosen[w] = by_id[w]
            continue
        exact = [i for i in catalog if i["name"].strip() == w]
        fuzzy = [i for i in catalog if w in i["name"]]
        hit = exact or fuzzy
        if not hit:
            raise ServiceError(f"该数据集下没有名为「{w}」的指标。可用：{'、'.join(i['name'] for i in catalog[:10])}……")
        if len(hit) > 1 and not exact:
            raise ServiceError(f"「{w}」匹配多个指标：{'、'.join(i['name'] for i in hit[:6])}，请写得更具体或直接给指标 id")
        chosen[hit[0]["id"]] = hit[0]
    return list(chosen.values())


async def nbs_fetch(
    db: str,
    cid: str,
    indicators: list[str],
    regions: list[str] | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    n: int = 8,
    root_id: str | None = None,
) -> Table:
    """取数 → 整洁长表。regions 对全国口径的数据集可省略。"""
    _check_db(db)
    _check_id(cid, "cid")
    if not indicators:
        raise ServiceError("indicators 不能为空：给指标 id 或指标名称")
    try:
        async with _nbs() as c:
            catalog = await c.indicators(cid)
            region_info = await c.regions(cid)
            chosen_inds = _resolve_indicators(catalog, indicators)
            if region_info["catalogs"]:
                if not regions:
                    raise ServiceError("这是分地区的数据集，需要指定 regions（如 [\"河北\", \"北京\"] 或 [\"全部\"]）")
                areas = resolve_regions(region_info["regions"], regions)
            else:
                areas = [dict(NATIONAL)]
            if root_id is None:
                # root_id 是整棵树的全局根（各库相同），不是库节点自己的 id；从任一节点的 treeinfo 里取
                roots = await c.tree(db, "")
                root_id = next((r["root_id"] for r in roots if r.get("root_id")), "")
                if not valid_id(root_id):
                    raise ServiceError("无法确定 root_id，请传入 root_id（见 nbs_find / nbs_browse 结果里的 root_id）")
            else:
                _check_id(root_id, "root_id")
            try:
                records = await c.table(
                    code=db,
                    cid=cid,
                    indicator_ids=[i["id"] for i in chosen_inds],
                    root_id=root_id,
                    areas=areas,
                    n=max(1, min(n, 30)),
                    start=start,
                    end=end,
                )
            except ValueError as exc:
                raise ServiceError(str(exc)) from exc
    except (SourceError, TimeoutError) as exc:
        raise _wrap(exc) from exc

    rows = [
        [r["期别"], r["期别代码"], r["地区"], r["指标"], to_number(r["数值"]), r["单位"]]
        for r in records
    ]
    rows.sort(key=lambda r: (r[3], r[2], r[1]))  # 指标、地区、期别：便于阅读与透视
    names = "、".join(clean_name(i["name"], i.get("unit")) for i in chosen_inds[:3]) + (
        "等" if len(chosen_inds) > 3 else ""
    )
    where = "、".join(a["text"] for a in areas[:3]) + ("等" if len(areas) > 3 else "")
    return Table(
        title=f"{names}（{where}）",
        rows=rows,
        meta={
            "来源": "国家统计局·国家数据（data.stats.gov.cn）",
            "数据库": f"{DATABASES[db][0]}（代码 {db}）",
            "网址": PAGE,
            "取数时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "指标数": len(chosen_inds),
            "地区数": len(areas),
            "说明": "数值版权归国家统计局，请以官网为准；跨年比较前请核对统计口径与修订。",
        },
    )
