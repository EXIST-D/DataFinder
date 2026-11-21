import pytest

from datafinder.catalog import SearchRequest, search
from datafinder.catalog.tree import InvalidNode, axes_overview, tree_children


def kids(cat, axis, node=None, **req):
    return tree_children(cat, SearchRequest(**req), axis=axis, node=node)


def by_id(res):
    return {c["id"]: c for c in res["children"]}


def test_axes_overview_gives_total_and_three_axes(cat):
    o = axes_overview(cat)
    assert o["total"] == search(cat, SearchRequest()).total
    assert [a["id"] for a in o["axes"]] == ["topic", "region", "source"]


def test_topic_axis_counts_distinct_resources_and_has_an_unclassified_node(cat):
    r = kids(cat, "topic")
    c = by_id(r)
    assert c["cnstat.macro"]["count"] == 4 and c["cnstat.industry"]["count"] == 4
    assert c["cnstat.population"]["count"] == 1
    assert c["cnstat.industry"]["has_children"] and c["cnstat.industry"]["select"] == {
        "topic": "cnstat.industry"
    }
    # 没有任何主题的资源（库、系列）不能凭空消失
    assert c["_none"]["name"] == "未归类" and c["_none"]["count"] == 2
    assert c["_none"]["select"] is None and r["children"][-1]["id"] == "_none"
    assert r["total"] == 9


def test_topic_children_and_breadcrumb(cat):
    r = kids(cat, "topic", "cnstat.industry")
    assert [p["id"] for p in r["path"]] == ["cnstat.industry"]
    assert by_id(r)["cnstat.industry.value_added"]["count"] == 3


def test_own_axis_filter_does_not_hide_siblings_but_other_filters_apply(cat):
    # 选了"人口"主题后，主题轴上"工业"兄弟节点仍可见（与分面计数同一口径）
    assert (
        by_id(kids(cat, "topic", topic_ids=["cnstat.population"]))["cnstat.industry"]["count"] == 4
    )
    # 其他筛选（关键词）会改变计数
    c = by_id(kids(cat, "topic", query="工业增加值"))
    assert c["cnstat.industry"]["count"] <= 4 and "cnstat.population" not in c


@pytest.mark.parametrize("axis", ["topic", "source"])
def test_root_count_matches_search_total_for_single_valued_axes(cat, axis):
    for req in ({}, {"query": "工业增加值"}, {"kinds": ["indicator"]}):
        r = kids(cat, axis, **req)
        total = search(cat, SearchRequest(**req)).total
        assert r["total"] == total
        if axis == "source":  # 每个资源恰属一个领域
            assert sum(c["count"] for c in r["children"]) == total


def test_source_axis_walks_the_real_hierarchy_and_pages_leaves(cat):
    d = by_id(kids(cat, "source"))["domain:official_statistics_cn"]
    assert d["name"] == "中国官方统计"
    srcs = by_id(kids(cat, "source", d["id"]))
    assert srcs["source:nbs"]["count"] + srcs["source:yb.sd"]["count"] == d["count"]
    nbs = kids(cat, "source", "source:nbs")
    assert [p["name"] for p in nbs["path"]] == ["中国官方统计", "国家统计局"]
    db = by_id(nbs)["res:nbs:database:6"]
    assert db["has_children"]
    ds = kids(cat, "source", "res:nbs:dataset:6/c1")
    leaves = ds["children"]
    assert [x["leaf"] for x in leaves] == [True, True] and ds["total_children"] == 2
    assert leaves[0]["select"] == {"inspect": leaves[0]["id"].removeprefix("res:")}
    page2 = tree_children(
        cat, SearchRequest(), axis="source", node="res:nbs:dataset:6/c1", limit=1, offset=1
    )
    assert len(page2["children"]) == 1 and page2["total_children"] == 2
    assert page2["children"][0]["id"] == leaves[1]["id"]


def test_source_axis_counts_follow_the_filters(cat):
    r = by_id(kids(cat, "source", "source:nbs", query="工业增加值"))
    assert sum(c["count"] for c in r.values()) >= 1
    # 只有年鉴的筛选下，国家统计局下面没有命中
    assert kids(cat, "source", "source:nbs", topic_ids=["cnstat.population"])["children"] == []


def test_region_axis_is_coverage_semantic_and_expands_on_demand(cat):
    top = by_id(kids(cat, "region"))
    assert top["CN"]["count"] == 5 and top["CN"]["has_children"]
    provinces = by_id(kids(cat, "region", "CN"))
    assert provinces["CN-HE"]["count"] == 4  # 全国分省库覆盖河北
    assert provinces["CN-SD"]["count"] == 8  # 另加山东年鉴的资源
    sd = by_id(kids(cat, "region", "CN-SD"))
    assert (
        sd["CN-370100"]["name"] == "济南市" and sd["CN-370100"]["count"] == 1
    )  # 只有按地级报告的表覆盖它
    assert not any(c["count"] == 0 for c in sd.values())  # 计数为 0 的不返回
    assert [p["name"] for p in kids(cat, "region", "CN-SD")["path"]] == ["中国", "山东"]


def test_region_axis_honours_the_same_granularity_rule_as_search(cat):
    # 济南市只被"按地级报告"的资源覆盖，与按地区检索的结果一致
    node = by_id(kids(cat, "region", "CN-SD"))["CN-370100"]
    assert (
        node["count"] == search(cat, SearchRequest(region_ids=["CN-370100"], mode="strict")).total
    )


def test_invalid_axis_or_node_is_an_error_not_an_empty_tree(cat):
    with pytest.raises(InvalidNode):
        kids(cat, "colour")
    with pytest.raises(InvalidNode):
        kids(cat, "topic", "cnstat.nope")
    with pytest.raises(InvalidNode):
        kids(cat, "region", "ZZ-NOPE")
    with pytest.raises(InvalidNode):
        kids(cat, "source", "res:nope")
    with pytest.raises(InvalidNode):
        kids(cat, "source", "bad-node")


def test_generic_only_keywords_give_an_empty_tree_with_a_hint(cat):
    r = kids(cat, "topic", query="数据")
    assert r["children"] == [] and r["total"] == 0 and r["hints"]
