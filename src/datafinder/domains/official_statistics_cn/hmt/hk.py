"""香港政府统计处（C&SD）适配器：目录（discover）与实时取数（fetch）。

香港统计处为每张网上统计表提供公开 JSON 接口（`/api/get.php?id=<表号>&lang=sc&full_series=1`，免认证、
带 `header`/`dataSet`），并在"可用接口一览"页的 JSON-LD（schema.org DataCatalog）里列出全部表（2026-11-21 共 639 张）。
目录只来自这份官方清单；取数是一次 GET，返回的完整序列转成整洁长表。限速 ≥1 秒；失败按类型报错，不重试绕过。
"""

from __future__ import annotations

import json
import re
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
from datafinder.engine.http import Http
from datafinder.tables import Table, to_number

from .. import DOMAIN_ID
from ..classify import topics_for

SOURCE_ID = "hk.csd"
LIST_URL = "https://www.censtatd.gov.hk/sc/API.html"
API_URL = "https://www.censtatd.gov.hk/api/get.php"
HOME_URL = "https://www.censtatd.gov.hk/sc/"
TERMS_URL = "https://www.censtatd.gov.hk/tc/page_31.html"
LICENSE = f"香港政府统计处使用条款（{TERMS_URL}）"
REGION = "CN-HK"
_LD_JSON = re.compile(r"<script[^>]+application/ld\+json[^>]*>\s*(\{.*?\})\s*</script>", re.S)
_TOTAL = {"total", "合计", "总计", "整体", "all"}


# 香港统计处自己的栏目名 → 本领域一级主题（通用规则按"国家统计局式"名称写，认不出这些栏目；只补规则没命中的）
TOPIC_HINTS = (
    ("零售", "cnstat.commerce"),
    ("批发", "cnstat.commerce"),
    ("膳食及住宿", "cnstat.commerce"),
    ("旅游", "cnstat.culture"),
    ("文化", "cnstat.culture"),
    ("建造", "cnstat.investment"),
    ("地产", "cnstat.investment"),
    ("房屋", "cnstat.investment"),
    ("金融", "cnstat.finance"),
    ("保险", "cnstat.finance"),
    ("货币", "cnstat.finance"),
    ("公共财政", "cnstat.fiscal"),
    ("国际收支", "cnstat.trade"),
    ("贸易", "cnstat.trade"),
    ("运输", "cnstat.transport"),
    ("资讯", "cnstat.innovation"),
    ("研究及发展", "cnstat.innovation"),
    ("科学及技术", "cnstat.innovation"),
    ("制造", "cnstat.industry"),
    ("能源", "cnstat.energy"),
    ("卫生", "cnstat.health"),
    ("社会福利", "cnstat.health"),
    ("教育", "cnstat.education"),
    ("国民经济核算", "cnstat.macro"),
    ("物价", "cnstat.prices"),
    ("劳工", "cnstat.livelihood"),
    ("工资", "cnstat.livelihood"),
    ("住户", "cnstat.livelihood"),
    ("人口", "cnstat.population"),
)


def _topics(cats: list[str], title: str) -> list[TopicLink]:
    target = Target(top=cats[0] if cats else title, path=[*cats, title], title=title)
    found = topics_for(target)
    if not found:  # 规则没命中才用栏目名补，避免给已经分好的表再叠一堆主题
        text = " ".join([*cats, title])
        hinted = list(dict.fromkeys(t for k, t in TOPIC_HINTS if k in text))
        found = [TopicLink(t, "rule") for t in hinted[:2]]
    return found


def parse_catalog(html: str) -> list[dict[str, Any]]:
    """从"可用接口一览"页的 JSON-LD 里取出全部统计表（identifier、name、keywords）。"""
    for block in _LD_JSON.findall(html):
        try:
            graph = json.loads(block).get("@graph", [])
        except ValueError:
            continue
        for node in graph:
            if node.get("@type") == "DataCatalog":
                return [d for d in node.get("dataset", []) if d.get("identifier")]
    return []


def api_url(table_id: str, lang: str = "sc") -> str:
    return f"{API_URL}?id={table_id}&lang={lang}&full_series=1"


def table_entry(d: dict[str, Any]) -> Entry:
    table_id, title = d["identifier"], d["name"].strip()
    cats = [k for k in d.get("keywords", []) if k != table_id][:2]  # [大类, 小类]
    return Entry(
        SOURCE_ID,
        "dataset",
        table_id,
        title,
        parent_id=make_id(SOURCE_ID, "portal", "home"),
        path=" › ".join(cats) or None,
        landing_url=api_url(table_id),
        license=LICENSE,
        verification_level="source_asserted",
        topics=_topics(cats, title),
        regions=[RegionLink(REGION, "covers", None, "exact", "structure")],
        distributions=[
            Distribution(
                "api",
                api_url(table_id),
                "json",
                "tabular",
                note="实时取数：返回该表完整序列（JSON）",
            )
        ],
        extra={"table_id": table_id, "category": cats},
    )


def home_entry() -> Entry:
    return Entry(
        SOURCE_ID,
        "portal",
        "home",
        "香港政府统计处",
        landing_url=HOME_URL,
        license=LICENSE,
        verification_level="source_asserted",
        regions=[RegionLink(REGION, "covers", None, "exact", "structure")],
        distributions=[Distribution("page", HOME_URL)],
    )


def to_table(table_id: str, payload: dict[str, Any], params: FetchParams) -> Table:
    """接口 JSON → 整洁长表。每个维度 X 有代码 X 与说明 XDesc；指标 = 表名 + 非"合计"的维度说明。"""
    header = payload.get("header") or {}
    status = header.get("status") or {}
    if status.get("code", 0) != 0:
        raise AdapterError(
            f"香港统计处接口返回失败：{status.get('description') or status.get('name')}"
        )
    records = [r for r in payload.get("dataSet", []) if r.get("figure") is not None]
    title = (header.get("title") or table_id).strip()
    dims = sorted({k[:-4] for r in records for k in r if k.endswith("Desc") and k != "svDesc"})
    many_sv = len({(r.get("sv"), r.get("svDesc")) for r in records}) > 1
    by_freq: dict[str, list[str]] = {}
    for r in records:
        by_freq.setdefault(str(r.get("freq") or ""), []).append(str(r["period"]))
    keep: set[tuple[str, str]] = set()
    for freq, periods in by_freq.items():
        distinct = sorted(set(periods))
        if params.start or params.end:
            lo, hi = params.start or "", params.end or "\uffff"
            chosen = [p for p in distinct if lo <= p[: len(lo)] and p[: len(hi)] <= hi]
        else:
            chosen = distinct[-max(1, params.periods) :]
        keep |= {(freq, p) for p in chosen}
    rows = []
    for r in records:
        if (str(r.get("freq") or ""), str(r["period"])) not in keep:
            continue
        parts = [
            str(r[f"{d}Desc"]).strip()
            for d in dims
            if r.get(f"{d}Desc") and str(r[f"{d}Desc"]).strip().lower() not in _TOTAL
        ]
        sv = str(r.get("svDesc") or "").strip()
        label = " · ".join([title, *parts, *([sv] if many_sv and sv else [])])
        rows.append([str(r["period"]), str(r["period"]), "香港", label, to_number(r["figure"]), sv])
    rows.sort(key=lambda x: (x[3], x[1]))
    return Table(
        title,
        rows,
        meta={
            "来源": "香港政府统计处",
            "网址": api_url(table_id),
            "表号": table_id,
            "说明": "；".join(str(n) for n in header.get("tablenote", []) if n),
            "许可": LICENSE,
        },
    )


class HkAdapter(Adapter):
    id = "hk"
    domain_id = DOMAIN_ID
    capabilities = frozenset({CAP_DISCOVER, CAP_FETCH})

    def sources(self) -> list[SourceInfo]:
        return [SourceInfo(SOURCE_ID, "香港政府统计处", "香港政府统计处", HOME_URL, "open_api")]

    async def discover(self, cat: Catalog, opts: DiscoverOptions) -> DiscoverResult:
        self.register_sources(cat)
        cat.upsert([home_entry()])
        run = cat.start_run(SOURCE_ID, "discover")
        http = Http(default_interval=1.0)
        try:
            code, content = await http.get_bytes(LIST_URL, timeout=60.0)
        finally:
            await http.aclose()
        tables = parse_catalog(content.decode("utf-8", errors="replace")) if code == 200 else []
        if not tables:
            cat.finish_run(run, "error", message="没有解析出统计表清单")
            raise AdapterError("香港统计处的接口一览页没有解析出统计表（页面结构可能变了）")
        entries = [table_entry(d) for d in tables]
        cat.upsert(entries)
        cat.finish_run(run, "ok", seen=len(entries), changed=len(entries))
        opts.say(f"香港政府统计处：{len(entries)} 张统计表")
        return DiscoverResult("done", len(entries), len(entries), 1)

    async def fetch(self, cat: Catalog, entry: Entry, params: FetchParams) -> Table:
        if entry.kind != "dataset":
            raise AdapterError(f"{entry.kind} 不能取数：请选一张统计表（dataset）")
        table_id = entry.extra["table_id"]
        http = Http(default_interval=1.0)
        try:
            payload = await http.get_json(api_url(table_id), timeout=120.0)
        finally:
            await http.aclose()
        return to_table(table_id, payload, params)
