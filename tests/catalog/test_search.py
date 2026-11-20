import pytest

from datafinder.catalog import InvalidFacet, SearchRequest, facet_counts, search


def S(cat, **kw):
    return search(cat, SearchRequest(**kw))


def ids(resp):
    return [r["resource_id"] for r in resp.results]


def test_keyword_search_explains_why_and_uses_thesaurus(cat):
    r = S(cat, query="工业增加值", kinds=["indicator"])
    assert ids(r)[0] in ("nbs:indicator:6/c1/i1", "nbs:indicator:3/c9/i1")
    top = r.results[0]
    assert any("工业增加值" in w for w in top["why_matched"]) and top["delivery"] == "generate"
    # 术语词表：研发 → 研究与试验发展（没有这个词的资源不会被拉进来）
    assert S(cat, query="研发").total >= 0


def test_topic_filter_includes_descendants_and_resolves_names(cat):
    by_l1 = S(cat, topic_ids=["cnstat.industry"], kinds=["indicator", "table"])
    assert {"nbs:indicator:6/c1/i1", "yb.sd:table:2025/14-08"} <= set(ids(by_l1))
    by_l2 = S(cat, topic_ids=["工业增加值"], kinds=["indicator", "table"])  # 用中文名也能解析（L2）
    assert set(ids(by_l2)) <= set(ids(by_l1)) | {"yb.sd:table:2024/14-08"}
    assert "nbs:indicator:6/c1/i2" not in ids(by_l2)  # 第二产业增加值不在工业增加值 L2 下


def test_region_intersects_uses_descendants_scope_but_not_exact_national(cat):
    r = S(cat, region_ids=["河北"], kinds=["indicator"], query="增加值")
    got = set(ids(r))
    assert "nbs:indicator:6/c1/i1" in got  # 全国分省库覆盖河北
    assert "nbs:indicator:3/c9/i1" not in got  # 全国汇总（exact CN）不包含河北
    sd = S(cat, region_ids=["CN-SD"], kinds=["table"], query="工业增加值")
    assert ids(sd) and all(i.startswith("yb.sd:") for i in ids(sd))
    assert S(cat, region_ids=["CN-HE"], kinds=["table"], query="工业增加值").total == 0  # 山东年鉴不覆盖河北


def test_exact_region_match_ignores_ancestor_scope(cat):
    exact = S(cat, region_ids=["CN-HE"], region_match="exact", kinds=["indicator"], query="增加值")
    assert exact.results == []


def test_same_table_across_editions_is_merged_with_all_versions_listed(cat):
    r = S(cat, query="规模以上工业增加值", kinds=["table"])
    assert len(r.results) == 1
    item = r.results[0]
    assert item["versions"] == [2025, 2024] and item["resource_id"] == "yb.sd:table:2025/14-08"
    assert [e["file_format"] for e in item["editions"]] == ["jpg", "xls"]
    assert item["delivery"] == "download" and item["usability"] == "image"
    assert len(S(cat, query="规模以上工业增加值", kinds=["table"], group_versions=False).results) == 2


def test_discovery_keeps_unknowns_but_says_what_is_missing_strict_drops_them(cat):
    disc = S(cat, query="规模以上工业增加值", kinds=["table"], period_start=2015, period_end=2020)
    assert disc.results and any("观测期未知（仅有出版年 2025）" in m for m in disc.results[0]["missing_requirements"])
    strict = S(cat, query="规模以上工业增加值", kinds=["table"], period_start=2015, period_end=2020, mode="strict")
    assert strict.results == [] and any("discovery" in h for h in strict.hints)
    known = S(cat, query="工业增加值", kinds=["indicator"], period_start=2000, period_end=2010, mode="strict")
    assert {"nbs:indicator:6/c1/i1", "nbs:indicator:3/c9/i1"} <= set(ids(known))  # 观测期已知且重叠：strict 也满足


def test_ongoing_period_overlaps_any_later_year(cat):
    r = S(cat, query="第二产业增加值", kinds=["indicator"], period_start=2030, period_end=2031, mode="strict")
    assert "nbs:indicator:6/c1/i2" in ids(r)  # 起 1990、至今（end 为空）：与未来年份重叠
    assert "nbs:indicator:3/c9/i1" not in ids(r)
    ended = S(cat, query="工业增加值", kinds=["indicator"], period_start=2030, mode="strict")
    assert "nbs:indicator:3/c9/i1" not in ids(ended)  # 止于 2023


def test_granularity_strict_requires_confirmed_evidence_and_discovery_flags_inferred(cat):
    strict = S(cat, query="工业增加值", kinds=["table"], geo_granularity="prefecture", mode="strict")
    assert strict.results == []  # 山东年鉴表"各市"粒度只是标题推断
    disc = S(cat, query="工业增加值", kinds=["table"], geo_granularity="prefecture")
    assert disc.results and any("由标题推断，未验证" in m for m in disc.results[0]["missing_requirements"])
    prov = S(cat, query="工业增加值", kinds=["indicator"], geo_granularity="province", mode="strict")
    assert ids(prov) == ["nbs:indicator:6/c1/i1"]  # 分省库的省级粒度由来源结构决定


def test_delivery_filter(cat):
    dl = S(cat, delivery=["download"], kinds=["table"])
    assert {r["delivery"] for r in dl.results} == {"download"}
    link = S(cat, delivery=["link"], kinds=["table", "series"])
    assert "yb.sd:series:CN-SD" in ids(link) and "yb.sd:table:2025/2-1" in ids(link)  # 无分发入口也是 link
    assert S(cat, delivery=["generate"], kinds=["table"]).results == []


def test_facets_exclude_their_own_filter_and_dedupe_resources(cat):
    r = S(cat, query="增加值", topic_ids=["cnstat.industry"], kinds=["indicator", "table"])
    topics = {t["id"]: t["count"] for t in r.facets["topics"]}
    assert topics["cnstat.macro"] >= 1  # 选了"工业"，主题分面仍显示其他主题的候选数
    assert topics["cnstat.industry"] >= 2
    kinds_ = {k["id"]: k["count"] for k in r.facets["kinds"]}
    assert kinds_["indicator"] >= 2 and "table" in kinds_
    assert {d["id"] for d in r.facets["delivery"]} <= {"download", "generate", "link"}
    regions_ = {x["id"] for x in S(cat, query="增加值").facets["regions"]}
    assert {"CN", "CN-SD"} <= regions_
    one = facet_counts(cat, SearchRequest(kinds=["indicator"]))
    counts = {k["id"]: k["count"] for k in one["kinds"]}  # 类型分面不受自己的筛选影响
    assert counts["indicator"] == 3 and counts["table"] == 3 and counts["series"] == 1


def test_invalid_facets_raise_actionable_errors(cat):
    for bad, msg in [
        (dict(topic_ids=["cnstat.nope"]), "未知主题"),
        (dict(region_ids=["火星"]), "未知地域"),
        (dict(kinds=["tablez"]), "未知资源类型"),
        (dict(delivery=["steal"]), "delivery"),
        (dict(mode="yolo"), "mode"),
        (dict(period_start=2030, period_end=2000), "period_start"),
        (dict(geo_granularity="street"), "geo_granularity"),
    ]:
        with pytest.raises(InvalidFacet, match=msg):
            S(cat, **bad)


def test_generic_only_query_returns_hint_not_garbage(cat):
    r = S(cat, query="统计数据分析评价")
    assert r.results == [] and r.hints and "无区分度" in r.hints[0]


def test_pure_facet_browse_without_keywords_and_paging(cat):
    page1 = S(cat, region_ids=["CN-SD"], kinds=["table"], limit=1)
    page2 = S(cat, region_ids=["CN-SD"], kinds=["table"], limit=1, offset=1)
    assert page1.total == 3 and len(page1.results) == 1 and page1.results[0]["resource_id"] != page2.results[0]["resource_id"]
    assert S(cat, kinds=["category"]).total == 0  # 导航节点默认/显式都不会凭空出现


def test_default_kinds_exclude_navigation_categories(cat):
    from datafinder.catalog import Entry

    cat.upsert([Entry("nbs", "category", "6/国民经济核算", "国民经济核算")])
    assert "nbs:category:6/国民经济核算" not in ids(S(cat, query="国民经济核算"))
    assert "nbs:category:6/国民经济核算" in ids(S(cat, query="国民经济核算", kinds=["category"]))
