"""台湾行政院主计总处「总体统计数据库」适配器：目录（discover，不联网）与实时取数（fetch）。

主计总处提供匿名的 SDMX 风格 JSON 接口：`webMain.aspx?sdmx/<功能代码>/<维度筛选>&startTime=&endTime=`，
维度筛选留空即取该表全部维度。功能代码取自官方《总体统计数据库 API 说明文件》附表一，并已逐个向接口核验
（data/tw_tables.yaml；不能返回数据的代码没有收录）。标题与维度名由繁体转成简体，便于用简体关键词检索。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from datafinder.catalog.adapters import (
    CAP_DISCOVER,
    CAP_FETCH,
    Adapter,
    AdapterError,
    DiscoverOptions,
    DiscoverResult,
    FetchParams,
    SourceInfo,
)
from datafinder.catalog.entities import Distribution, Entry, RegionLink, TopicLink, make_id
from datafinder.catalog.repository import Catalog
from datafinder.catalog.rules import Target
from datafinder.catalog.text import to_simplified
from datafinder.engine.http import Http
from datafinder.resources import load_package_yaml
from datafinder.tables import Table, to_number

from .. import DOMAIN_ID
from ..classify import topics_for

SOURCE_ID = "tw.dgbas"
HOME_URL = "https://www.stat.gov.tw/"
DB_URL = "https://nstatdb.dgbas.gov.tw/dgbasAll/webMain.aspx?sys=210"
API = "https://nstatdb.dgbas.gov.tw/dgbasAll/webMain.aspx?sdmx/"
LICENSE = "以台湾行政院主计总处网站声明为准（许可未逐条核实）"
REGION = "CN-TW"
_UNIT = re.compile(
    r"[（(]([^()（）]*(?:元|%|百分|人|件|点|指数|吨|公顷|户|美元|年)[^()（）]*)[）)]"
)

# 台湾主计总处的统计类别 → 本领域主题（通用规则按国家统计局式名称写，这些类别名需要补充）
CATEGORY_TOPICS = {
    "国民所得统计": ["cnstat.macro"],
    "物价统计": ["cnstat.prices"],
    "交通统计": ["cnstat.transport"],
    "人口统计": ["cnstat.population"],
    "卫生统计": ["cnstat.health"],
    "证券统计": ["cnstat.finance"],
    "金融统计": ["cnstat.finance"],
    "农业统计": ["cnstat.agriculture"],
    "家庭收支统计": ["cnstat.livelihood"],
    "财政统计": ["cnstat.fiscal"],
    "环保统计": ["cnstat.environment"],
    "景气指标统计": ["cnstat.macro"],
}


def api_url(code: str, start: str, end: str) -> str:
    return f"{API}{code}/&startTime={start}&endTime={end}"


def table_entries() -> list[Entry]:
    data = load_package_yaml(f"datafinder.domains.{DOMAIN_ID}", "tw_tables.yaml")
    out = []
    for t in data["tables"]:
        code, category, title = t["code"], to_simplified(t["category"]), to_simplified(t["name"])
        topics = topics_for(Target(top=category, path=[category, title], title=title))
        have = {x.topic_id for x in topics}
        topics += [TopicLink(x, "rule") for x in CATEGORY_TOPICS.get(category, []) if x not in have]
        out.append(
            Entry(
                SOURCE_ID,
                "dataset",
                code,
                title,
                parent_id=make_id(SOURCE_ID, "portal", "home"),
                path=category,
                landing_url=f"{DB_URL}&funid={code}",
                license=LICENSE,
                verification_level="observed",  # 功能代码已逐个向接口核验（见 data/tw_tables.yaml）
                topics=topics,
                regions=[RegionLink(REGION, "covers", None, "exact", "structure")],
                distributions=[
                    Distribution(
                        "api",
                        api_url(code, "<起>", "<止>"),
                        "json",
                        "tabular",
                        note="实时取数（SDMX 风格 JSON）；维度留空取全部",
                    )
                ],
                extra={"code": code},
            )
        )
    return out


def home_entries() -> list[Entry]:
    regions = [RegionLink(REGION, "covers", None, "exact", "structure")]
    return [
        Entry(
            SOURCE_ID,
            "portal",
            "home",
            "台湾行政院主计总处",
            landing_url=HOME_URL,
            license=LICENSE,
            verification_level="source_asserted",
            regions=regions,
            distributions=[Distribution("page", HOME_URL)],
        ),
        Entry(
            SOURCE_ID,
            "portal",
            "db",
            "台湾总体统计数据库",
            parent_id=make_id(SOURCE_ID, "portal", "home"),
            landing_url=DB_URL,
            license=LICENSE,
            verification_level="source_asserted",
            regions=regions,
            distributions=[Distribution("page", DB_URL)],
        ),
    ]


def to_table(code: str, payload: dict[str, Any], params: FetchParams) -> Table:
    data = payload.get("data") or {}
    structure = data.get("structure") or {}
    sets = data.get("dataSets") or []
    if not sets or not structure:
        raise AdapterError("主计总处接口没有返回数据（该表或该期间可能没有数据）")
    title = to_simplified(str(structure.get("name") or code)).strip()
    series_dims = structure["dimensions"]["series"]
    periods = structure["dimensions"]["observation"][0]["values"]  # 按时间升序
    cells: list[tuple[int, str, float | str]] = []  # (期别下标, 指标, 数值)
    units: dict[str, str] = {}
    for key, s in sets[0]["series"].items():
        idx = [int(i) for i in key.split(":")]
        names = [
            to_simplified(d["values"][i]["name"]).strip()
            for d, i in zip(series_dims, idx, strict=False)
        ]
        label = " · ".join([title, *names])
        # 单位随该序列自己的维度名（同一张表里有的序列是金额、有的是指数）
        units[label] = next((m.group(1) for n in names if (m := _UNIT.search(n))), "")
        for i, vals in s["observations"].items():
            if vals and vals[0] is not None:
                cells.append((int(i), label, to_number(vals[0])))
    # 接口对"还没有数据的期别"填 0（如未到的年份）：整期全是 0 的期别不算观测，先剔除再取"最近 N 期"
    live = sorted({i for i, _, v in cells if v != 0})
    if not (params.start or params.end):
        live = live[-max(1, params.periods) :]
    keep = set(live)
    rows = [
        [str(periods[i]["id"]), str(periods[i]["id"]), "台湾", label, v, units[label]]
        for i, label, v in cells
        if i in keep
    ]
    rows.sort(key=lambda r: (r[3], r[1]))
    return Table(
        title,
        rows,
        meta={
            "来源": "台湾行政院主计总处总体统计数据库",
            "网址": f"{DB_URL}&funid={code}",
            "表号": code,
            "说明": "期别为西元年（月度/季度形如 2024-M3、2024-Q1）；名称已由繁体转为简体",
            "许可": LICENSE,
        },
    )


class TwAdapter(Adapter):
    id = "tw"
    domain_id = DOMAIN_ID
    capabilities = frozenset({CAP_DISCOVER, CAP_FETCH})

    def sources(self) -> list[SourceInfo]:
        return [
            SourceInfo(SOURCE_ID, "台湾总体统计数据库", "台湾行政院主计总处", HOME_URL, "open_api")
        ]

    async def discover(self, cat: Catalog, opts: DiscoverOptions) -> DiscoverResult:
        """目录来自随包数据文件（功能代码已核验），不联网。"""
        self.register_sources(cat)
        entries = [*home_entries(), *table_entries()]
        cat.upsert(entries)
        opts.say(f"台湾总体统计数据库：{len(entries) - 2} 张统计表（来自已核验的功能代码清单）")
        return DiscoverResult("done", len(entries), len(entries), 0)

    async def fetch(self, cat: Catalog, entry: Entry, params: FetchParams) -> Table:
        if entry.kind != "dataset":
            raise AdapterError(f"{entry.kind} 不能取数：请选一张统计表（dataset）")
        code = entry.extra["code"]
        this_year = date.today().year
        start = params.start or str(this_year - max(2, params.periods + 1))
        end = params.end or str(this_year)
        http = Http(default_interval=1.0)
        try:
            code_, content = await http.get_bytes(api_url(code, start, end), timeout=60.0)
        finally:
            await http.aclose()
        text = content.decode("utf-8", errors="replace")
        if code_ != 200 or not text.lstrip().startswith("{"):
            raise AdapterError(f"主计总处接口没有返回数据：{text[:60].strip() or code_}")
        import json

        return to_table(code, json.loads(text), params)
