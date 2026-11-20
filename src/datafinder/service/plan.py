"""找数方案（旗舰能力）：把研究主题拆成'去哪找数据'。"""

from __future__ import annotations

from typing import Any

from ..links import build_links
from ..nbs import index as nbs_index
from ..portals.browse import Filters, browse, portal_dict
from ..query.plan import parse, province_zh_name
from ..query.tokenize import has_cjk
from ..store import connect, portal_count
from ..yearbook import index as yb_index
from .errors import ServiceError

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
    # 统计年鉴里的相关数据表（带"有哪些年份可查"）
    yearbooks: list[dict] = []
    yb_hint = None
    yb = yb_index.connect_index()
    try:
        if yb_index.stats(yb)["tables"]:
            yearbooks = yb_index.search(yb, topic, region=region_codes[0] if region_codes else None, limit=per_group)
        else:
            yb_hint = "尚未建立年鉴目录：运行 `datafinder yearbook index` 后重试。"
    finally:
        yb.close()
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
    if yearbooks:
        steps.append("yearbooks 给出年鉴数据表的各版次链接（多为图片/网页表格）；要可直接分析的数值优先用 direct 里的指标 + nbs_fetch。")
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
            "yearbooks": {
                "title": "统计年鉴里的相关数据表（可按年份版次查）",
                "items": yearbooks,
                "hint": yb_hint,
            },
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
