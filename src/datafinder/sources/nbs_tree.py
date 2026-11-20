"""国家统计局发布库：分类树、指标清单、地区、取值（给"中国统计"页用）。

接口（均已实测，无需鉴权，需 Referer；2026-11-20）：
  GET  /new/queryIndexTreeAsync?pid=&code=        分类树，按层逐级展开
  GET  /new/queryIndicatorsByCid?cid=&dt=&name=   叶子数据集下的指标清单（含统计口径）
  GET  /getDaCatalogTreeByIndicatorCid?indicatorCid=   该数据集可选的地区分类
  GET  /getDasByDaCatalogId?daCid=                地区清单（省份、城市等）
  POST /stream/esData                             取值（多地区用 showType "3"）

**访问礼貌**（实测教训）：前面有防火墙，并发爬取树会触发 JS 验证页。所以
- 分类树只按用户点击逐级加载，缓存 24 小时，绝不批量抓取；
- 同一主机串行、间隔 ≥0.8 秒（由 Http 的 min_interval 保证）；
- 一旦识别到验证页，写入故障标记，10 分钟内不再访问，不重试、不绕过。

注意：全局搜索接口对"分省/城市"等库只返回计数、不返回行（实测），所以这些库的数据只能走这里。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ..engine.cache import Cache
from ..engine.errors import ShapeError, SourceError
from ..engine.http import Http

BASE = "https://data.stats.gov.cn/dg/website/publicrelease/web/external"
PAGE = "https://data.stats.gov.cn/dg/website/page.html"
HEADERS = {
    "client": "pc",
    "Accept": "application/json, text/plain, */*",
    "Referer": PAGE,
}
SOURCE_ID = "nbs"
TREE_TTL = 24 * 3600
INDICATOR_TTL = 12 * 3600

# 数据库代码 → 名称（官方 13 个库）与时间类型（决定取值时 dts 的写法）
DATABASES: dict[str, tuple[str, str]] = {
    "1": ("月度数据", "MM"),
    "2": ("季度数据", "SS"),
    "3": ("年度数据", "YY"),
    "4": ("分省月度数据", "MM"),
    "5": ("分省季度数据", "SS"),
    "6": ("分省年度数据", "YY"),
    "7": ("主要城市月度价格", "MM"),
    "8": ("主要城市年度数据", "YY"),
    "9": ("港澳台月度数据", "MM"),
    "10": ("港澳台年度数据", "YY"),
    "12": ("三大经济体月度数据", "MM"),
    "13": ("国际市场月度商品价格", "MM"),
    "14": ("主要国家年度数据", "YY"),
}

_ID = re.compile(r"^[0-9a-f]{32}$")
_AREA = re.compile(r"^\d{12}$")
MAX_AREAS = 8
MAX_PERIODS = 30


NATIONAL = {"text": "全国", "value": "000000000000"}


def valid_id(value: str) -> bool:
    return bool(_ID.match(value))


def valid_area(value: str) -> bool:
    return bool(_AREA.match(value))


def dts_window(dt_type: str, n: int, today: date | None = None) -> str:
    """取值用的时间区间。实测 `LAST5` 之类的写法不被支持，必须给明确区间。

    窗口取 2n 个时间单位：数据发布有滞后，未发布的期会返回空值，解析时过滤后再取最近 n 期。
    """
    today = today or date.today()
    n = max(1, min(n, MAX_PERIODS)) * 2
    if dt_type == "YY":
        return f"{today.year - n + 1}YY-{today.year}YY"
    months = today.year * 12 + today.month - 1
    if dt_type == "MM":
        start = months - n + 1
        return f"{start // 12}{start % 12 + 1:02d}MM-{today.year}{today.month:02d}MM"
    if dt_type == "SS":
        quarters = today.year * 4 + (today.month - 1) // 3
        start = quarters - n + 1
        return f"{start // 4}{start % 4 + 1:02d}SS-{today.year}{(today.month - 1) // 3 + 1:02d}SS"
    raise ValueError(f"未知时间类型 {dt_type}")


class NbsClient:
    def __init__(self, http: Http, cache: Cache) -> None:
        self.http = http
        self.cache = cache

    async def _call(self, method: str, path: str, **kw: Any) -> Any:
        reason = self.cache.resting(SOURCE_ID)
        if reason:
            raise SourceError("rate_limited", f"国家统计局接口暂时休息（{reason}），请稍后再试")
        try:
            j = await self.http.request_json(
                method, BASE + path, headers={**HEADERS, **kw.pop("headers", {})}, **kw
            )
        except SourceError as exc:
            if exc.is_outage:
                self.cache.mark_down(SOURCE_ID, str(exc))
            raise
        if not isinstance(j, dict) or "data" not in j:
            raise ShapeError("国家统计局响应缺少 data")
        if j.get("success") is False:
            raise SourceError("http", f"国家统计局返回失败：{j.get('message')}")
        return j["data"]

    # ── 分类树 ──
    async def tree(self, code: str, pid: str = "") -> list[dict]:
        if code not in DATABASES:
            raise ValueError(f"未知数据库代码 {code}")
        key = f"nbs:tree:{code}:{pid}"
        cached = self.cache.kv_get(key, max_age=TREE_TTL)
        if cached is not None:
            return cached
        data = await self._call(
            "GET", "/new/queryIndexTreeAsync", params={"pid": pid, "code": code}
        )
        if not isinstance(data, list):
            raise ShapeError("分类树响应 data 不是列表")
        nodes = [
            {
                "id": n["_id"],
                "name": str(n.get("name") or n.get("_name") or "").strip(),
                "is_leaf": bool(n.get("isLeaf")),
                "sdate": n.get("sdate"),
                "edate": n.get("edate"),
                "root_id": str(n.get("treeinfo_globalid") or "").split(".")[0],
            }
            for n in data
            if "_id" in n
        ]
        self.cache.kv_put(key, nodes)
        return nodes

    # ── 指标清单 ──
    async def indicators(self, cid: str) -> list[dict]:
        key = f"nbs:ind:{cid}"
        cached = self.cache.kv_get(key, max_age=INDICATOR_TTL)
        if cached is not None:
            return cached
        data = await self._call(
            "GET", "/new/queryIndicatorsByCid", params={"cid": cid, "dt": "", "name": ""}
        )
        rows = (data or {}).get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ShapeError("指标清单响应缺少 data.list")
        out = [
            {
                "id": r["_id"],
                "name": str(r.get("i_showname") or r.get("_name") or "").strip(),
                "unit": r.get("du_name"),
                "mark": (r.get("i_mark") or r.get("i_annotation") or None),
            }
            for r in rows
            if "_id" in r
        ]
        self.cache.kv_put(key, out)
        return out

    # ── 地区 ──
    async def regions(self, cid: str) -> dict:
        """该数据集可选的地区分类，及第一个分类（通常是"全部地区"）下的地区清单。"""
        key = f"nbs:reg:{cid}"
        cached = self.cache.kv_get(key, max_age=TREE_TTL)
        if cached is not None:
            return cached
        cats = await self._call(
            "GET", "/getDaCatalogTreeByIndicatorCid", params={"indicatorCid": cid}
        )
        if not isinstance(cats, list) or not cats:
            # 没有地区分类 = 全国口径的数据集（实测 das 传"全国"才能取到值，传空列表返回空）
            result = {"catalogs": [], "regions": [dict(NATIONAL)]}
        else:
            first = cats[0]["_id"]
            das = await self._call("GET", "/getDasByDaCatalogId", params={"daCid": first})
            result = {
                "catalogs": [{"id": c["_id"], "name": c.get("name")} for c in cats if "_id" in c],
                "regions": [
                    {"text": r["name_text"], "value": r["name_value"]}
                    for r in (das if isinstance(das, list) else [])
                    if r.get("name_text") and r.get("name_value")
                ],
            }
        self.cache.kv_put(key, result)
        return result

    # ── 取值 ──
    async def series(
        self,
        *,
        code: str,
        cid: str,
        indicator_id: str,
        root_id: str,
        areas: list[dict],
        n: int = 8,
        today: date | None = None,
    ) -> dict:
        if code not in DATABASES:
            raise ValueError(f"未知数据库代码 {code}")
        areas = areas[:MAX_AREAS]
        body = {
            "cid": cid,
            "indicatorIds": [indicator_id],
            "daCatalogId": "",
            "das": areas,
            "showType": "3",  # 多地区必须用 3；返回里地区字段是 area/areaCode
            "dts": [dts_window(DATABASES[code][1], n, today)],
            "rootId": root_id,
        }
        data = await self._call(
            "POST",
            "/stream/esData",
            json_body=body,
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )
        return parse_series(data, n)


def parse_series(data: Any, n: int) -> dict:
    """esData 响应 → 表格：每期一行、每地区一列。过滤未发布（空值）的期，取最近 n 期。"""
    if not isinstance(data, list):
        raise ShapeError("取值响应 data 不是列表")
    periods: list[dict] = []
    areas: list[str] = []
    unit = indicator = None
    for p in sorted(data, key=lambda x: str(x.get("code", "")), reverse=True):
        values: dict[str, str] = {}
        for v in p.get("values") or []:
            # showType 3 用 area；单地区老写法用 da_name，两种都兼容
            area = v.get("area") or v.get("da_name") or "全国"
            val = v.get("value")
            if val in (None, ""):
                continue
            values[str(area)] = str(val)
            if area not in areas:
                areas.append(str(area))
            unit = unit or v.get("du_name")
            indicator = indicator or str(v.get("i_showname") or v.get("_name") or "").strip()
        if values:
            periods.append({"code": p.get("code"), "name": p.get("name"), "values": values})
    return {"indicator": indicator, "unit": unit, "areas": areas, "periods": periods[:n]}
