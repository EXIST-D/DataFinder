"""国家统计局取数：地区/指标名称解析 + 取表（整洁长表）。

用户和助手常用简称（"河北"、"工业增加值"），这里负责把它们对应到该数据集真实支持的地区代码与指标 id；
解析不了就报错并给出可选项，不猜。
"""

from __future__ import annotations

import re
from datetime import datetime

from datafinder.catalog.adapters import AdapterError
from datafinder.tables import Table, to_number

from .client import (
    DATABASES,
    MAX_AREAS_TABLE,
    NATIONAL,
    PAGE,
    NbsClient,
    clean_name,
    valid_area,
    valid_id,
)

_REGION_SUFFIX = re.compile(r"(维吾尔自治区|壮族自治区|回族自治区|特别行政区|自治区|省|市)$")
ALL_WORDS = ("全部", "all", "*", "全部省份", "所有省份")


def _norm_region(name: str) -> str:
    return _REGION_SUFFIX.sub("", name.strip())


def resolve_regions(available: list[dict], wanted: list[str]) -> list[dict]:
    """把"河北"、"河北省"、"130000000000"、"全部" 解析成该数据集支持的地区。"""
    by_code = {r["value"]: r for r in available}
    chosen: dict[str, dict] = {}
    for w in (x.strip() for x in wanted):
        if not w:
            continue
        if w in ALL_WORDS:
            chosen.update({r["value"]: r for r in available if r["value"] != NATIONAL["value"]})
            continue
        if valid_area(w):
            if w not in by_code:
                raise AdapterError(f"该数据集不支持地区代码 {w}")
            chosen[w] = by_code[w]
            continue
        key = _norm_region(w)
        match = [r for r in available if r["text"] == w] or [r for r in available if _norm_region(r["text"]) == key]
        if not match:
            sample = "、".join(r["text"] for r in available[:12])
            raise AdapterError(f"该数据集没有地区「{w}」。可选地区示例：{sample}……")
        chosen[match[0]["value"]] = match[0]
    if not chosen:
        raise AdapterError("没有指定地区（regions 不能为空）")
    if len(chosen) > MAX_AREAS_TABLE:
        raise AdapterError(f"一次最多取 {MAX_AREAS_TABLE} 个地区")
    return list(chosen.values())


def resolve_indicators(catalog: list[dict], wanted: list[str]) -> list[dict]:
    chosen: dict[str, dict] = {}
    by_id = {i["id"]: i for i in catalog}
    for w in (x.strip() for x in wanted):
        if valid_id(w):
            if w not in by_id:
                raise AdapterError(f"该数据集下没有指标 {w}，请用 inspect 查看该数据集的指标清单")
            chosen[w] = by_id[w]
            continue
        exact = [i for i in catalog if i["name"].strip() == w or clean_name(i["name"], i.get("unit")) == w]
        hit = exact or [i for i in catalog if w in i["name"]]
        if not hit:
            raise AdapterError(f"该数据集下没有名为「{w}」的指标。可用：{'、'.join(i['name'] for i in catalog[:10])}……")
        if len(hit) > 1 and not exact:
            raise AdapterError(f"「{w}」匹配多个指标：{'、'.join(i['name'] for i in hit[:6])}，请写得更具体或直接给指标 id")
        chosen[hit[0]["id"]] = hit[0]
    return list(chosen.values())


async def fetch_table(
    client: NbsClient, *, db: str, cid: str, root_id: str | None, indicators: list[str], regions: list[str],
    start: str | None, end: str | None, periods: int,
) -> Table:
    if db not in DATABASES:
        raise AdapterError(f"未知数据库代码 {db!r}")
    if not valid_id(cid):
        raise AdapterError("cid 格式不对（应为 32 位十六进制 id）")
    if not indicators:
        raise AdapterError("没有指定指标：给指标 id 或指标名称")
    catalog = await client.indicators(cid)
    region_info = await client.regions(cid)
    chosen = resolve_indicators(catalog, indicators)
    if region_info["catalogs"]:
        if not regions:
            raise AdapterError('这是分地区的数据集，需要指定 regions（如 ["河北", "北京"] 或 ["全部"]）')
        areas = resolve_regions(region_info["regions"], regions)
    else:
        areas = [dict(NATIONAL)]
    if not root_id or not valid_id(root_id):
        # root_id 是整棵树的全局根（各库相同），不是库节点自己的 id；从任一节点的 treeinfo 里取
        roots = await client.tree(db, "")
        root_id = next((r["root_id"] for r in roots if r.get("root_id")), "")
        if not valid_id(root_id):
            raise AdapterError("无法确定 root_id；请重新建立该库的目录索引后再试")
    try:
        records = await client.table(
            code=db, cid=cid, indicator_ids=[i["id"] for i in chosen], root_id=root_id, areas=areas,
            n=max(1, min(periods, 30)), start=start, end=end,
        )
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc

    # 期望的地区/指标用"接口实际返回的名称"表示（同一地区请求时叫"北京市"、返回时叫"北京"），覆盖检查才对得上
    seen_region = {r["地区代码"]: r["地区"] for r in records}
    seen_indicator = {r["指标ID"]: r["指标"] for r in records}
    expected = {
        "regions": [seen_region.get(a["value"], a["text"]) for a in areas],
        "indicators": [seen_indicator.get(i["id"], clean_name(i["name"], i.get("unit"))) for i in chosen],
    }
    rows = [[r["期别"], r["期别代码"], r["地区"], r["指标"], to_number(r["数值"]), r["单位"]] for r in records]
    rows.sort(key=lambda r: (r[3], r[2], r[1]))  # 指标、地区、期别：便于阅读与透视
    names = "、".join(clean_name(i["name"], i.get("unit")) for i in chosen[:3]) + ("等" if len(chosen) > 3 else "")
    where = "、".join(a["text"] for a in areas[:3]) + ("等" if len(areas) > 3 else "")
    return Table(
        title=f"{names}（{where}）",
        rows=rows,
        meta={
            "来源": "国家统计局·国家数据（data.stats.gov.cn）",
            "数据库": f"{DATABASES[db][0]}（代码 {db}）",
            "网址": PAGE,
            "取数时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "指标数": len(chosen),
            "地区数": len(areas),
            "说明": "数值版权归国家统计局，请以官网为准；跨年比较前请核对统计口径与修订。",
            "expected": expected,
        },
    )
