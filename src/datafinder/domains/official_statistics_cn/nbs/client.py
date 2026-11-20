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

from datafinder.engine.cache import Cache
from datafinder.engine.errors import ShapeError, SourceError
from datafinder.engine.http import Http

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
MAX_AREAS = 8  # 网页一次最多选的地区数
MAX_AREAS_TABLE = 40  # 取表（CLI/MCP）一次最多的地区数，够装下 31 个省
MAX_PERIODS = 30
MAX_INDICATORS = 10
_PERIOD = {"YY": re.compile(r"^\d{4}$"), "MM": re.compile(r"^\d{6}$"), "SS": re.compile(r"^\d{4}0[1-4]$")}


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


def explicit_window(dt_type: str, start: str | None, end: str | None) -> str:
    """明确的起止区间。缺一端时：起点默认 10 年前，终点默认今年（给不存在的未来期别也安全，空值会被过滤）。"""
    pattern = _PERIOD.get(dt_type)
    if pattern is None:
        raise ValueError(f"未知时间类型 {dt_type}")
    today = date.today()
    default_end = {"YY": f"{today.year}", "MM": f"{today.year}{today.month:02d}",
                   "SS": f"{today.year}{(today.month - 1) // 3 + 1:02d}"}[dt_type]
    default_start = {"YY": f"{today.year - 10}", "MM": f"{today.year - 5}01", "SS": f"{today.year - 5}01"}[dt_type]
    a, b = (start or default_start), (end or default_end)
    for label, v in (("起", a), ("止", b)):
        if not pattern.match(v):
            hint = {"YY": "年度写 2015", "MM": "月度写 201501", "SS": "季度写 202401（2024 年第 1 季）"}[dt_type]
            raise ValueError(f"{label}期格式不对：{v!r}（{hint}）")
    if a > b:
        raise ValueError("起期不能晚于止期")
    return f"{a}{dt_type}-{b}{dt_type}"


class NbsClient:
    def __init__(self, http: Http, cache: Cache) -> None:
        self.http = http
        self.cache = cache
        self.calls = 0  # 实际发出的网络请求数（缓存命中不算）

    async def _call(self, method: str, path: str, **kw: Any) -> Any:
        self.calls += 1
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

    # ── 全国级关键词搜索（官方全局搜索；分省/城市库只返回计数，所以这里只有全国口径）──
    async def search_national(self, term: str, limit: int = 15) -> list[dict]:
        data = await self._call(
            "GET", "/query", params={"code": "", "pagenum": 1, "pageSize": limit, "search": term}
        )
        rows = (data or {}).get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ShapeError("搜索响应 data.data 不是列表")
        out = []
        for r in rows:
            if "cid" not in r or "indic_id" not in r:
                continue
            code = {"YY": "3", "SS": "2", "MM": "1"}.get(str(r.get("dt_type") or ""))
            out.append(
                {
                    "indicator": re.sub(r"\s+", " ", str(r.get("show_name") or "")).strip(),
                    "area": str(r.get("da_name") or "全国"),
                    "latest_period": str(r.get("dt_name") or r.get("dt") or ""),
                    "latest_value": r.get("value"),
                    "kind": str(r.get("type_text") or ""),
                    "db": code,
                    "cid": str(r["cid"]),
                    "indicator_id": str(r["indic_id"]),
                    "root_id": str(r.get("treeinfo_globalid") or "").split(".")[0],
                }
            )
        return out

    # ── 取值 ──
    async def _es_data(
        self,
        *,
        cid: str,
        indicator_ids: list[str],
        root_id: str,
        areas: list[dict],
        dts: str,
    ) -> Any:
        body = {
            "cid": cid,
            "indicatorIds": indicator_ids,
            "daCatalogId": "",
            "das": areas[:MAX_AREAS_TABLE],
            "showType": "3",  # 多地区必须用 3；返回里地区字段是 area/areaCode
            "dts": [dts],
            "rootId": root_id,
        }
        return await self._call(
            "POST",
            "/stream/esData",
            json_body=body,
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )

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
        data = await self._es_data(
            cid=cid,
            indicator_ids=[indicator_id],
            root_id=root_id,
            areas=areas[:MAX_AREAS],
            dts=dts_window(DATABASES[code][1], n, today),
        )
        return parse_series(data, n)

    async def table(
        self,
        *,
        code: str,
        cid: str,
        indicator_ids: list[str],
        root_id: str,
        areas: list[dict],
        n: int = 8,
        start: str | None = None,
        end: str | None = None,
        today: date | None = None,
    ) -> list[dict]:
        """整洁长表的行字典：期别、期别代码、地区、指标、数值、单位。

        给了 start/end 就取这个区间（年度 `2015`、月度 `201501`、季度 `202401` 表示 2024 年第 1 季），
        否则取最近 n 期。一次可取多个指标（最多 MAX_INDICATORS 个，每个指标发一次请求）。
        """
        if code not in DATABASES:
            raise ValueError(f"未知数据库代码 {code}")
        if not 1 <= len(indicator_ids) <= MAX_INDICATORS:
            raise ValueError(f"一次取 1–{MAX_INDICATORS} 个指标")
        dt_type = DATABASES[code][1]
        dts = explicit_window(dt_type, start, end) if (start or end) else dts_window(dt_type, n, today)
        # 实测：showType 3（多地区）下一次传多个指标，接口每个 地区×期别 只回其中一个指标（其余被吞掉，
        # 且不报错）。所以一个指标一个请求，串行（有限速），宁可慢一点也不能静默丢数据。
        data: list[dict] = []
        for indicator_id in indicator_ids:
            part = await self._es_data(
                cid=cid, indicator_ids=[indicator_id], root_id=root_id, areas=areas, dts=dts
            )
            if not isinstance(part, list):
                raise ShapeError("取值响应 data 不是列表")
            data.extend(part)
        return parse_tidy(data, None if (start or end) else n)


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


_UNIT_TAIL = re.compile(r"\s*[(（]([^()（）]*)[)）]\s*$")


def clean_name(show: Any, unit: Any) -> str:
    """'工业增加值 (亿元) ' → '工业增加值'（单位另有一列，名称里不再重复）。"""
    text = str(show or "").strip()
    m = _UNIT_TAIL.search(text)
    if m and (not unit or m.group(1).strip() == str(unit).strip()):
        text = text[: m.start()].strip()
    return text


def parse_tidy(data: Any, n: int | None) -> list[dict]:
    """esData 响应 → 整洁长表行。过滤未发布（空值）的观测；n 给定时每个 指标×地区 只保留最近 n 期。"""
    if not isinstance(data, list):
        raise ShapeError("取值响应 data 不是列表")
    rows: list[dict] = []
    for p in sorted(data, key=lambda x: str(x.get("code", "")), reverse=True):
        for v in p.get("values") or []:
            val = v.get("value")
            if val in (None, ""):
                continue
            # 注意：`_name` 是该指标所属的标准名（如"工业增加值"行里写的是"国内生产总值"），
            # 官网上显示、用户要找的是 `i_showname`（实测）
            indicator = clean_name(v.get("i_showname"), v.get("du_name")) or str(
                v.get("_name") or ""
            ).strip()
            rows.append(
                {
                    "期别": str(p.get("name") or p.get("code") or ""),
                    "期别代码": str(p.get("code") or ""),
                    "地区": str(v.get("area") or v.get("da_name") or "全国").strip(),
                    "指标": indicator,
                    "数值": val,
                    "单位": str(v.get("du_name") or ""),
                    "指标ID": str(v.get("_id") or ""),
                    "地区代码": str(v.get("areaCode") or v.get("da") or ""),
                }
            )
    if n is not None:
        kept: dict[tuple[str, str], int] = {}
        out = []
        for r in rows:  # rows 已按期别新→旧
            key = (r["指标ID"] or r["指标"], r["地区代码"] or r["地区"])
            kept[key] = kept.get(key, 0) + 1
            if kept[key] <= n:
                out.append(r)
        rows = out
    return rows
