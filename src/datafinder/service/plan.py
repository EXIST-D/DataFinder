"""找数方案：把研究/分析主题拆成"去哪找数据"——指标、数据表、官方入口、继续检索的入口，并给出统计分析建议。

纯本地（目录与门户库都在本机），不联网，所以很快，也不会触发任何站点的访问防护。
完全基于通用目录检索：没有任何领域专有逻辑，领域包接入后自动出现在结果里。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from datafinder.catalog import SearchRequest
from datafinder.catalog import search as catalog_search

from ..links import build_links
from ..portals.browse import Filters, browse, portal_dict
from ..query.plan import industries_in, parse, province_zh_name
from ..query.tokenize import has_cjk
from ..store import connect, portal_count
from .common import open_catalog
from .errors import ServiceError, guard

_ASSESS = ("评价", "评估", "指数", "水平", "效率", "竞争力", "质量", "绩效", "发展程度", "综合")
_REGIONAL_INTENT = re.compile(r"各省|分省|各地区|各地|各市|省际|区域|地区差异|地区间|省份")


def _analysis_hints(topic: str, *, regions: list[str]) -> list[str]:
    hints = [
        "先明确分析单元与时间范围（如：某省 vs 全国/邻省，近 10 年），再回头选指标；指标口径在不同年份之间可能调整，需在论文里说明。",
        "描述性统计建议：各指标的样本量、均值、中位数、标准差、变异系数、极值，以及首末期与年均复合增长率（`describe` 可直接算）。",
    ]
    if not regions or len(regions) > 1:
        hints.append(
            "做地区横向比较：同一指标、同一年份对各地区排名，并看与全国均值的差距；总量指标与人均/比率指标分开解读。"
        )
    hints.append(
        "时间趋势：用折线图展示关键指标，标注政策节点；比率类指标优先用同比/年均增速，总量类用绝对值与增速并列。"
    )
    if any(w in topic for w in _ASSESS):
        hints += [
            "若题目是'评价/指数/水平'类：先搭指标体系（一级维度→二级指标），统一方向（正向/逆向）并标准化，再考虑熵值法或 TOPSIS 等客观赋权，附稳健性检验（换赋权方法、剔除指标）。",
            "指标体系里每个指标都要有可追溯的官方来源（见 indicators 与 official_portals）；缺失的指标要说明替代口径或剔除理由。",
        ]
    hints.append(
        "统计数据常有修订：引用时写明取数日期与来源名称；不同来源（年鉴 vs 数据库）数字可能有出入，以最新官方发布为准。"
    )
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


def _regional_portals(conn: sqlite3.Connection, region_codes: list[str], size: int) -> list[dict]:
    """目标地区：统计局和开放平台排最前，其余官方入口随后。"""
    out: list[dict] = []
    for code in region_codes[:2]:
        for label in ("stats_bureau", "open_platform", None):
            rows, _ = browse(conn, Filters(region=code, label=label), page=1, size=size)
            out += [portal_dict(r) for r in rows]
    return _dedupe_portals(out)[:size]


def _official_portals(
    conn: sqlite3.Connection, domains: list[str], country: str | None, size: int
) -> list[dict]:
    """领域权威门户：对口的国家部委 → 统计类官方入口 → 其余官方门户。"""
    ministries: list[dict] = []
    others: list[dict] = []
    for d in domains or [None]:
        rows, _ = browse(
            conn, Filters(domain=d, country=country, label="ministry"), page=1, size=size
        )
        ministries += [portal_dict(r) for r in rows]
        rows, _ = browse(conn, Filters(domain=d, country=country, official=True), page=1, size=size)
        others += [portal_dict(r) for r in rows]
    stats: list[dict] = []
    if country == "CN":  # 统计类入口对任何中国主题都相关（年鉴、公报、统计数据库）
        rows, _ = browse(
            conn, Filters(domain="statistics", country="CN", official=True), page=1, size=3
        )
        stats = [portal_dict(r) for r in rows]
    return _dedupe_portals(ministries, stats, others)[: size + 4]


def _search_entries(topic: str) -> list[dict]:
    keep = {"dateno", "google-ds", "scidb", "europa", "datacite"}
    return [
        {"name": link["name"], "url": link["url"], "scope": link["scope"], "note": link["note"]}
        for g in build_links(topic)
        for link in g["links"]
        if link["id"] in keep or g["id"] == "china"
    ]


def _fetch_hint(item: dict[str, Any], region_names: list[str]) -> dict[str, Any] | None:
    """能"生成"的资源给出可直接用的取数参数；其余不给。"""
    if item["delivery"] != "generate":
        return None
    args: dict[str, Any] = {"resource_id": item["resource_id"]}
    if any(
        r["scope_mode"] == "descendants"
        and r["relation"] == "covers"
        and r["granularity"] in ("province", "prefecture")
        for r in item["regions"]
    ):
        args["regions"] = region_names[:1] or ["全部"]
    if item["kind"] == "dataset":
        args["indicators"] = ["<先 inspect 数据集看指标清单>"]
    return {"tool": "fetch", "args": args}


def plan_sources(topic: str, region: str | None = None, *, per_group: int = 8) -> dict[str, Any]:
    topic = topic.strip()
    if not topic:
        raise ServiceError("topic 不能为空", code="INVALID_ARGUMENT")
    with guard():
        plan = parse(f"{region} {topic}" if region and region not in topic else topic)
        country = plan.countries[0] if plan.countries else ("CN" if has_cjk(topic) else None)
        domains = list(dict.fromkeys([*plan.industries, *industries_in(topic)]))[:3]
        region_codes = list(plan.regions)
        region_names = [province_zh_name(c) or c for c in region_codes]
        intent = bool(region_codes) or bool(_REGIONAL_INTENT.search(topic))

        def run(**kw: Any) -> list[dict[str, Any]]:
            req = SearchRequest(query=topic, limit=per_group, include_facets=False, **kw)
            return catalog_search(cat, req).to_dict()["results"]

        with open_catalog() as cat:
            if region_codes:
                regional = run(kinds=["indicator"], region_ids=region_codes)
                regional_tables = run(kinds=["table"], region_ids=region_codes)
            elif intent:
                regional = run(kinds=["indicator"], geo_granularity="province")
                regional_tables = run(kinds=["table"], geo_granularity="province")
            else:
                regional, regional_tables = [], []
            # 全国口径也要给：做地区研究时总要和全国对照（它们覆盖的是"全国"，不含该地区，所以单独查）
            national = (
                run(kinds=["indicator"], region_ids=["CN"], region_match="exact")
                if intent
                else run(kinds=["indicator"])
            )
            national_tables = (
                run(kinds=["table"], region_ids=["CN"], region_match="exact")
                if intent
                else run(kinds=["table"])
            )
        indicators = list({r["resource_id"]: r for r in [*regional, *national]}.values())
        tables = list({r["resource_id"]: r for r in [*regional_tables, *national_tables]}.values())
        for item in indicators:
            item["fetch"] = _fetch_hint(item, region_names)

        conn = connect()
        try:
            if not portal_count(conn):
                raise ServiceError(
                    "门户库为空，请先在终端运行 datafinder sync",
                    code="INDEX_NOT_READY",
                    next_action="datafinder sync",
                )
            regional_portals = _regional_portals(conn, region_codes, per_group)
            official_portals = _official_portals(conn, domains, country, per_group)
        finally:
            conn.close()

        steps = []
        if any(i["delivery"] == "generate" for i in indicators):
            steps.append(
                "对 indicators 里的指标调用 fetch 取表（先不带 confirm 预览，确认后 confirm=true；会导出 CSV/Excel 并给出描述统计与覆盖检查）。"
            )
        if any(t["delivery"] == "download" for t in tables):
            steps.append(
                "tables 里 delivery=download 的可用 download 下载；注意 usability=image 的只是图片，不能直接分析。"
            )
        steps.append(
            "用 search 加主题/地域/类型筛选继续找；官方入口里 verify=blocked 的站点拒绝自动访问，需用浏览器打开。"
        )
        steps.append("需要更多文件型数据时，用 search 的 live=true 搜科研与开放数据平台。")
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
                "indicators": {"title": "可取数的指标", "items": indicators},
                "tables": {"title": "数据表（年鉴等，可按版次查）", "items": tables},
                "regional_portals": {"title": "目标地区的官方入口", "items": regional_portals},
                "official_portals": {"title": "相关领域的官方/权威门户", "items": official_portals},
                "search_entries": {"title": "继续检索的入口", "items": _search_entries(topic)},
            },
            "caveats": [
                "门户与链接来自本地目录，除标注 verify=ok 的外未逐条核验；verify=blocked 表示站点拒绝自动访问，请用浏览器打开。",
                "目录只收录名称、口径与链接，不含数值；真实数值以 fetch 实时取到的为准。",
            ],
            "analysis_hints": _analysis_hints(topic, regions=region_codes),
            "next_steps": steps,
        }
