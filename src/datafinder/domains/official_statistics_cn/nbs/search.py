"""国家统计局新版数据发布库（data.stats.gov.cn，2026-03-27 上线，**试运行期**）。

接口无正式文档，参考社区实现（Ddhjx-code/national_data、aahl/skills 的 cn-stats），并经实测：
- 搜索  GET  /dg/website/publicrelease/web/external/query?search=词&pagenum=1&pageSize=30
        返回 指标 + 地区 + 最新值；**只有全国级**；匹配是模糊的（搜"城镇化率"可能一条都没有，
        搜"国内生产总值"很准）。精度交给我们自己的排序，所以这里每个中文概念单独搜一次再合并。
- 取值  POST /dg/website/publicrelease/web/external/stream/esData  JSON 请求体，需 Referer。
        实测年度数据可取到 2020–2024 的时间序列。

实验性：接口可能随时变；失败只影响本来源。礼貌限速。
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from typing import Any

from datafinder.engine.errors import ShapeError, SourceError
from datafinder.models import Dataset, Resource
from datafinder.query.plan import QueryPlan
from datafinder.sources.base import Ctx, Source, SourceSpec

BASE = "https://data.stats.gov.cn/dg/website/publicrelease/web/external"
PAGE = "https://data.stats.gov.cn/dg/website/page.html"
HEADERS = {
    "client": "pc",
    "Accept": "application/json, text/plain, */*",
    "Referer": PAGE,
}
PAGE_SIZE = 30
_SPACES = re.compile(r"\s+")


def _series_range(dt: str, dt_type: str) -> tuple[str, str]:
    """示例请求体里用的时间范围：年度取近 5 年，月度取近 5 年，其他单点。"""
    if dt_type == "YY" and dt[:4].isdigit():
        return f"{int(dt[:4]) - 4}YY", f"{dt[:4]}YY"
    if dt_type == "MM" and len(dt) >= 6 and dt[:4].isdigit():
        return f"{int(dt[:4]) - 4}{dt[4:6]}MM", f"{dt[:6]}MM"
    return f"{dt}{dt_type}", f"{dt}{dt_type}"


def to_dataset(row: dict[str, Any]) -> Dataset | None:
    try:
        title = _SPACES.sub(" ", str(row["show_name"])).strip()
        cid, indic_id = str(row["cid"]), str(row["indic_id"])
    except KeyError as exc:
        raise ShapeError(f"国家统计局搜索结果缺少字段 {exc}") from exc
    if not title:
        return None
    area = str(row.get("da_name") or "全国")
    period = str(row.get("dt_name") or row.get("dt") or "") or None
    kind = str(row.get("type_text") or "")
    value = row.get("value")
    desc = f"国家统计局{kind}。{area}"
    if period:
        desc += f"，最新 {period}"
    if value not in (None, ""):
        desc += f"：{value}"
    if row.get("explain"):
        desc += f"。{row['explain']}"

    dt, dt_type = str(row.get("dt") or ""), str(row.get("dt_type") or "")
    start, end = _series_range(dt, dt_type)
    root = str(row.get("treeinfo_globalid") or "").split(".")[0]
    body = {
        "cid": cid,
        "indicatorIds": [indic_id],
        "daCatalogId": "",
        "das": [{"text": area, "value": str(row.get("da") or "000000000000")}],
        "showType": "1",
        "dts": [f"{start}-{end}"],
        "rootId": root,
    }
    note = f"POST，Content-Type: application/json，需带 Referer: {PAGE}"
    if dt_type not in ("YY", "MM"):
        note += "。年度、月度已实测；季度等的 dts 格式以官网为准"
    body_text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return Dataset(
        title=title,
        # 页面本身是单页应用，参数会被忽略；带上 cid 只为让每个指标有唯一地址（去重要用）
        url=f"{PAGE}?cid={cid}&indicatorId={indic_id}&da={row.get('da') or ''}",
        landing_url=PAGE,  # 单页应用打不开指标深链接：给人点的是官网入口，上面的 url 只用来区分每个指标
        source="nbs",
        description=desc,
        publisher="国家统计局",
        period=period,
        formats=["API"],
        resources=[
            Resource("api", "时间序列取值", f"{BASE}/stream/esData", "JSON", None, note, body_text),
            Resource(
                "api",
                "指标元数据",
                f"{BASE}/new/queryIndicatorsByCid?cid={cid}&dt=&name=",
                "JSON",
            ),
        ],
    )


class NbsSource(Source):
    def __init__(self, spec: SourceSpec) -> None:
        super().__init__(spec)

    def supports(self, plan: QueryPlan) -> bool:
        return bool(plan.zh_terms())

    def cache_query(self, plan: QueryPlan) -> str:
        return "|".join(plan.zh_terms())

    async def _query(self, ctx: Ctx, term: str) -> list[dict[str, Any]]:
        j = await ctx.http.get_json(
            f"{BASE}/query",
            params={"code": "", "pagenum": 1, "pageSize": PAGE_SIZE, "search": term},
            headers=HEADERS,
        )
        if not isinstance(j, dict) or "data" not in j:
            raise ShapeError("国家统计局搜索响应缺少 data")
        if j.get("success") is False:
            raise SourceError("http", f"国家统计局返回失败：{j.get('message')}")
        rows = (j["data"] or {}).get("data")
        if not isinstance(rows, list):
            raise ShapeError("国家统计局搜索响应 data.data 不是列表")
        return rows

    async def search(self, ctx: Ctx, plan: QueryPlan, limit: int) -> list[Dataset]:
        terms = plan.zh_terms()
        outcomes = await asyncio.gather(
            *(self._query(ctx, t) for t in terms), return_exceptions=True
        )
        lists = [o for o in outcomes if not isinstance(o, BaseException)]
        if not lists:
            first = next(o for o in outcomes if isinstance(o, BaseException))
            raise first
        # 各概念的结果交替取，保证每个词都有机会；同一指标+地区只留最新一期（搜索结果按新到旧）
        seen: set[tuple[str, str, str]] = set()
        out: list[Dataset] = []
        for row in itertools.chain.from_iterable(itertools.zip_longest(*lists)):
            if row is None:
                continue
            key = (str(row.get("indic_id")), str(row.get("da")), str(row.get("type_value")))
            if key in seen:
                continue
            seen.add(key)
            ds = to_dataset(row)
            if ds:
                out.append(ds)
            if len(out) >= limit:
                break
        return out
