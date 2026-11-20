"""服务层：MCP 与命令行共用的"找数 + 取数"能力。

所有函数返回可直接 JSON 序列化的 dict/list（或 Table），错误统一抛 `ServiceError`，
消息写给最终用户（或 AI 助手）看，告诉对方下一步怎么做。
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__, nbs_index
from .engine.cache import Cache
from .engine.errors import SourceError
from .engine.http import Http
from .enrich import enrich, lookup_portal
from .finder import MIN_INTERVAL, search_datasets
from .links import build_links
from .portals.browse import Filters, browse, portal_dict
from .query.plan import parse, province_zh_name
from .query.tokenize import has_cjk
from .sources.dataseek import find_executable
from .sources.nbs_tree import (
    DATABASES,
    MAX_AREAS_TABLE,
    NATIONAL,
    PAGE,
    NbsClient,
    clean_name,
    valid_area,
    valid_id,
)
from .sources.registry import load_specs
from .store import connect, db_path, get_meta, portal_count
from .tables import Table, describe, describe_markdown, exports_dir, save, to_number

PREVIEW_ROWS = 40


class ServiceError(Exception):
    """可直接展示给用户的错误。"""


# ───────────── 状态 ─────────────


def status() -> dict[str, Any]:
    conn = connect()
    try:
        portals = portal_count(conn)
        synced = get_meta(conn, "synced_at")
    finally:
        conn.close()
    idx = nbs_index.connect_index()
    try:
        index = nbs_index.stats(idx)
    finally:
        idx.close()
    return {
        "version": __version__,
        "portals": portals,
        "portals_synced_at": synced,
        "portals_hint": None if portals else "门户库为空，请先在终端运行 datafinder sync",
        "nbs_index": index,
        "nbs_index_hint": None
        if index["indicators"]
        else "国家统计局指标索引尚未建立：在终端运行 datafinder nbs index（约需十几分钟，只需一次）",
        "dataseek": bool(find_executable()),
        "exports_dir": str(exports_dir()),
        "database": str(db_path()),
    }


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


def table_result(table: Table, *, fmt: str | None = None, path: str | None = None, stats: bool = True) -> dict:
    """保存并整理成给助手/命令行展示的结果。"""
    saved = save(table, path, fmt)
    out: dict[str, Any] = {
        "saved_to": str(saved),
        "format": saved.suffix.lstrip("."),
        **table.to_dict(max_rows=PREVIEW_ROWS),
    }
    if stats and table.rows:
        d = describe(table)
        out["describe"] = d
        out["describe_markdown"] = describe_markdown(d)
    return out


def load_table(path: str | Path, *, anywhere: bool = False) -> Table:
    """读回导出的 CSV/xlsx（整洁长表）。anywhere=False 时只允许 exports 目录内的文件。"""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = exports_dir() / p
    p = p.resolve()
    if not anywhere and exports_dir().resolve() not in p.parents:
        raise ServiceError(f"出于安全，只能读取导出目录内的文件：{exports_dir()}")
    if not p.is_file():
        raise ServiceError(f"文件不存在：{p}")
    from .tables import COLUMNS

    rows: list[list[Any]] = []
    if p.suffix.lower() == ".csv":
        import csv

        with p.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            for r in reader:
                rows.append(r)
    elif p.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook

        ws = load_workbook(p, read_only=True, data_only=True)["数据"]
        it = ws.iter_rows(values_only=True)
        header = list(next(it, []))
        rows = [list(r) for r in it]
    else:
        raise ServiceError("只支持 .csv / .xlsx")
    if [str(h) for h in header[:5]] != COLUMNS[:5]:
        raise ServiceError("不是 DataFinder 导出的整洁长表（需要 期别、期别代码、地区、指标、数值 等列）")
    fixed = [[*r[:4], to_number(r[4]), *(r[5:6] or [""])] for r in rows if len(r) >= 5]
    return Table(title=p.stem, rows=fixed, meta={"来源文件": str(p)}, columns=list(header[:6]))


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


# ───────────── 找数方案（旗舰能力）─────────────

_ASSESS = ("评价", "评估", "指数", "水平", "效率", "竞争力", "质量", "绩效", "发展程度", "综合")


def _analysis_hints(topic: str, *, regions: list[str], has_regional_data: bool) -> list[str]:
    multi = not regions or len(regions) > 1
    hints = [
        "先明确分析单元与时间范围（如：某省 vs 全国/邻省，近 10 年），再回头选指标；指标口径在统计年份之间可能调整，需在论文里说明。",
        "描述性统计建议：各指标的样本量、均值、中位数、标准差、变异系数、极值，以及首末期与年均复合增长率（`describe_table` 可直接算）。",
    ]
    if has_regional_data or multi:
        hints.append("做地区横向比较：同一指标、同一年份对各省排名，并看与全国均值的差距；注意总量指标与人均/比率指标分开解读。")
    hints.append("时间趋势：用折线图展示关键指标，标注政策节点；比率类指标优先用同比/年均增速，总量类用绝对值与增速并列。")
    if any(w in topic for w in _ASSESS):
        hints += [
            "若题目是'评价/指数/水平'类：先搭指标体系（一级维度→二级指标），统一方向（正向/逆向）并标准化，再考虑熵值法或 TOPSIS 等客观赋权，附稳健性检验（换赋权方法、剔除指标）。",
            "指标体系里每个指标都要有可追溯的官方来源（见 sources 里的 direct 与 official_portals）；缺失的指标要说明替代口径或剔除理由。",
        ]
    hints.append("国家统计局的数据有修订：引用时写明取数日期与数据库名称；不同来源（统计年鉴 vs 数据库）数字可能有出入，以最新官方发布为准。")
    return hints


def _dedupe_portals(*groups: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for g in groups:
        for p in g:
            key = p["url"].rstrip("/").lower()
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def plan_sources(topic: str, region: str | None = None, *, per_group: int = 8) -> dict[str, Any]:
    """把一个研究/分析主题拆成"该去哪找数据"的方案：能直接取表的指标、官方门户、检索入口。

    纯本地、不联网（指标来自本地索引，门户来自本地门户库），所以很快，也不会触发访问防护。
    """
    topic = topic.strip()
    if not topic:
        raise ServiceError("topic 不能为空")
    plan = parse(f"{region} {topic}" if region and region not in topic else topic)
    country = plan.countries[0] if plan.countries else ("CN" if has_cjk(topic) else None)
    domains = plan.industries[:3]
    region_codes = list(plan.regions)

    conn = connect()
    try:
        if not portal_count(conn):
            raise ServiceError("门户库为空，请先在终端运行 datafinder sync")
        regional: list[dict] = []
        for code in region_codes[:2]:
            # 目标地区：统计局和开放平台排最前，其余官方入口随后
            for label in ("stats_bureau", "open_platform", None):
                rows, _ = browse(conn, Filters(region=code, label=label), page=1, size=per_group)
                regional += [portal_dict(r) for r in rows]
        national: list[dict] = []
        for d in domains or [None]:
            rows, _ = browse(
                conn, Filters(domain=d, country=country, official=True), page=1, size=per_group
            )
            national += [portal_dict(r) for r in rows]
        if country == "CN":  # 统计类入口对任何中国主题都相关（年鉴、公报、国家数据）
            rows, _ = browse(
                conn, Filters(domain="statistics", country="CN", official=True), page=1, size=3
            )
            national += [portal_dict(r) for r in rows]
    finally:
        conn.close()

    # 指标：分省（若问题带地区）与全国口径各取一批
    direct: list[dict] = []
    index_hint = None
    idx = nbs_index.connect_index()
    try:
        info = nbs_index.stats(idx)
        if info["indicators"]:
            if region_codes:
                direct += nbs_index.search(idx, topic, regional=True, limit=per_group)
            direct += nbs_index.search(idx, topic, regional=False, limit=per_group)
        else:
            index_hint = "尚未建立国家统计局指标索引，无法直接列出可取表的指标：运行 `datafinder nbs index` 后重试；或用 nbs_browse 逐级浏览。"
    finally:
        idx.close()
    region_names = [province_zh_name(c) or c for c in region_codes]
    for item in direct:
        item["fetch"] = {
            "tool": "nbs_fetch",
            "args": {
                "db": item["db"],
                "cid": item["cid"],
                "indicators": [item["indicator_id"]],
                **({"regions": region_names[:1] or ["全部"]} if item["regional"] else {}),
            },
        }

    links = build_links(topic)
    keep = {"dateno", "google-ds", "scidb", "europa", "datacite"}
    entries = [
        {"name": link["name"], "url": link["url"], "scope": link["scope"], "note": link["note"]}
        for g in links
        for link in g["links"]
        if link["id"] in keep or g["id"] == "china"
    ]

    steps = []
    if direct:
        steps.append("对 direct 里的指标调用 nbs_fetch 取表（会自动保存 CSV 并给出描述统计）。")
    steps.append("用 portals_list 按领域/地区继续浏览官方门户；统计年鉴、公报类数据通常在统计局官网。")
    steps.append("需要更多文件型数据时，用 datasets_search 搜科研与开放数据平台。")
    return {
        "topic": topic,
        "understanding": {
            "country": country,
            "regions": region_names,
            "region_codes": region_codes,
            "domains": domains,
            "concepts": [c.zh for c in plan.concepts if c.zh and not c.dimension],
            "years": plan.years,
        },
        "sources": {
            "direct": {"title": "可直接取表的国家统计局指标", "items": direct, "hint": index_hint},
            "regional_portals": {"title": "目标地区的官方入口", "items": _dedupe_portals(regional)[:per_group]},
            "official_portals": {"title": "相关领域的官方/权威门户", "items": _dedupe_portals(national)[: per_group + 4]},
            "search_entries": {"title": "继续检索的入口", "items": entries},
        },
        "caveats": [
            "门户与链接来自本地目录，除标注 verify=ok 的外未逐条核验；verify=blocked 表示站点拒绝自动访问，请用浏览器打开。",
            "指标索引只收录名称与口径，不含数值；真实数值以 nbs_fetch 实时取到的为准。",
        ],
        "analysis_hints": _analysis_hints(topic, regions=region_codes, has_regional_data=bool(region_codes)),
        "next_steps": steps,
    }
