import pytest

from datafinder.catalog import Catalog, Entry, RegionLink, make_id, portal_links
from datafinder.portals.sync import write_db
from datafinder.service import ServiceError
from datafinder.service import portal_tree as pt
from datafinder.store import connect

from ..conftest import P

pytestmark = pytest.mark.usefixtures("ready")


@pytest.fixture
def world(monkeypatch):
    cat = Catalog.open()
    cat.upsert_source(
        "nbs",
        domain_id="d",
        adapter_id="nbs",
        name="国家统计局",
        base_url="https://data.example.org/",
    )
    cat.upsert_source(
        "site.x",
        domain_id="d",
        adapter_id="site",
        name="某部委网站",
        base_url="https://x.example.org/",
    )
    prov = [RegionLink("CN", "covers", "province", "descendants", "structure")]
    cat.upsert(
        [
            Entry("nbs", "database", "6", "分省年度数据", regions=prov),
            Entry(
                "nbs",
                "dataset",
                "6/c1",
                "地区生产总值",
                parent_id=make_id("nbs", "database", "6"),
                regions=prov,
            ),
            Entry(
                "nbs",
                "indicator",
                "6/c1/i1",
                "工业增加值",
                parent_id=make_id("nbs", "dataset", "6/c1"),
                regions=prov,
            ),
            Entry(
                "site.x",
                "report",
                "r1",
                "某部委统计公报",
                regions=[RegionLink("CN", "covers", "country", "exact", "structure")],
            ),
        ]
    )
    cat.close()
    monkeypatch.setattr(portal_links, "_LINKS", {})
    big = [
        P(
            f"us{i}",
            f"US Portal {i:02d}",
            name_zh=None,
            country="US",
            region="US-CA" if i < 12 else None,
            owner_type="Local government" if i < 12 else "Academy",
        )
        for i in range(24)
    ]
    conn = connect()
    write_db(
        conn,
        [
            P(
                "datax",
                "Data X",
                name_zh="国家数据",
                url="https://data.example.org",
                country="CN",
                owner_type="Central government",
                catalog_type="Indicators catalog",
                has_api=True,
            ),
            P(
                "lonely",
                "Lonely",
                name_zh="孤立门户",
                url="https://lonely.example.org",
                country="CN",
                owner_type="Central government",
            ),
            P(
                "sdstat",
                "SD",
                name_zh="山东省统计局",
                url="http://sd.example.org",
                country="CN",
                region="CN-SD",
                owner_type="Regional government",
            ),
            P(
                "glob",
                "Global",
                name_zh="国际组织门户",
                country="World",
                owner_type="International",
            ),
            *big,
        ],
        meta={},
    )
    conn.close()


def ids(res):
    return [c["id"] for c in res["children"]]


def test_root_lists_countries_with_china_first_and_counts(world):
    r = pt.children()
    assert ids(r)[:2] == ["c:CN", "c:World"]
    cn = r["children"][0]
    assert (
        cn["name"] == "中国" and cn["count"] == 3 + 2 and cn["indexed"] == 3
    )  # 3 个门户 + 2 个门户库里没有记录的来源（某部委、山东年鉴）；已接入 3 个
    assert next(c for c in r["children"] if c["id"] == "c:US")["count"] == 24


def test_small_country_lists_portals_directly_and_marks_integration(world):
    kids = {c["id"]: c for c in pt.children("c:CN")["children"]}
    assert kids["p:datax"]["level"] == "indexed_and_fetchable" and kids["p:datax"]["has_children"]
    assert kids["p:lonely"]["level"] == "directory_only" and not kids["p:lonely"]["has_children"]
    assert kids["k:source:site.x"]["kind"] == "source"  # 目录里有、门户库没有记录的来源


def test_big_country_splits_into_regions_and_tiers_then_pages_portals(world):
    top = ids(pt.children("c:US"))
    assert top == [
        "t:US::4",
        "r:US:US-CA",
    ]  # 国家级的学术门户 12 个（≤FLAT 仍按机构类型拆）；加地区
    assert [c["name"] for c in pt.children("c:US")["children"]][0] == "科研与高校"
    ca = pt.children("r:US:US-CA")  # 12 个 ≤ FLAT：不再拆机构类型，直接列门户
    assert len(ca["children"]) == 12 and all(c["kind"] == "portal" for c in ca["children"])
    page = pt.children("t:US::4", limit=5, offset=5)
    assert len(page["children"]) == 5 and page["total_children"] == 12


def test_focus_jumps_to_the_page_that_contains_the_portal(world):
    full = pt.children("t:US::4", limit=100)
    target = full["children"][9]["portal_id"]
    r = pt.children("t:US::4", limit=4, focus=target)
    assert r["offset"] == 8 and target in [c["portal_id"] for c in r["children"]]


def test_indexed_portal_expands_to_sources_and_down_to_resources(world):
    src = pt.children("p:datax")["children"]
    assert [(c["id"], c["count"]) for c in src] == [("k:source:nbs", 5)]
    db = [c for c in pt.children("k:source:nbs")["children"] if c["kind"] == "container"]
    assert [c["name"] for c in db] == ["分省年度数据"]
    ds = pt.children(db[0]["id"])["children"]
    leaf = next(c for c in pt.children(ds[0]["id"])["children"] if c["name"] == "工业增加值")
    assert (
        leaf["kind"] == "resource"
        and leaf["resource_id"] == "nbs:indicator:6/c1/i1"
        and not leaf["has_children"]
    )


def test_indexed_only_hides_directory_only_portals(world):
    r = pt.children(indexed_only=True)
    assert ids(r) == ["c:CN"] and r["children"][0]["count"] == 3
    kids = ids(pt.children("c:CN", indexed_only=True))
    assert "p:lonely" not in kids and "p:datax" in kids


def test_search_gives_a_trail_that_leads_to_the_portal(world):
    hit = pt.search("US Portal 05")["results"][0]
    assert hit["node"]["portal_id"] == "us5" and hit["path"].startswith("美国")
    # 沿 trail 逐级展开，最后一步用 focus 就能在列表里找到它
    for node in hit["trail"][:-1]:
        assert pt.children(node)["children"]
    last = pt.children(hit["trail"][-1], focus="us5", limit=5)
    assert "us5" in [c.get("portal_id") for c in last["children"]]


def test_bad_node_and_empty_query_are_coded_errors(world):
    with pytest.raises(ServiceError) as e:
        pt.children("zzz:1")
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(ServiceError) as e:
        pt.children("p:nope")
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(ServiceError):
        pt.search("  ")


def test_tree_needs_the_portal_library(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFINDER_HOME", str(tmp_path / "empty2"))
    with pytest.raises(ServiceError) as e:
        pt.children()
    assert e.value.code == "INDEX_NOT_READY"


# ───────────── 左树 · 中列表 · 右概况，与筛选 ─────────────


def test_list_pages_portals_then_the_catalog_only_sources(world):
    first = pt.listing("c:CN", limit=3)
    assert first["total"] == 5 and [i["kind"] for i in first["items"]] == ["portal"] * 3
    second = pt.listing("c:CN", limit=3, offset=3)
    assert [i["kind"] for i in second["items"]] == [
        "source",
        "source",
    ]  # 门户库里没有记录的来源接在最后
    assert pt.listing("c:CN", sort="name")["items"][0]["kind"] == "portal"
    with pytest.raises(ServiceError) as e:
        pt.listing("p:datax")
    assert e.value.code == "INVALID_ARGUMENT"
    with pytest.raises(ServiceError):
        pt.listing("c:CN", sort="zzz")


def test_filters_apply_to_tree_counts_list_and_summary_alike(world):
    assert pt.listing("c:CN", kind="statistics")["total"] == 1
    assert [c["id"] for c in pt.children(kind="statistics")["children"]] == ["c:CN"]
    assert pt.children(kind="statistics")["children"][0]["count"] == 1
    assert pt.listing("c:CN", api=True)["total"] == 1
    assert (
        pt.listing("c:US", official=True)["total"] == 12
    )  # 12 个地方政府是官方，12 个科研与高校不是
    s = pt.summary("c:US", official=True)
    assert s["total"] == 12 and s["official"] == 12  # 概况里的数量与列表一致
    # 数据形态自己的分布不受数据形态筛选影响（筛选后其他形态仍可见、可切换）
    s = pt.summary("c:CN", kind="statistics")
    assert s["total"] == 1 and {k["key"]: k["count"] for k in s["kinds"]} == {
        "statistics": 1,
        "other": 2,
    }
    # 带门户属性的筛选时，门户库里没有记录的来源不出现
    assert pt.summary("c:CN")["total"] == 5 and pt.summary("c:CN", kind="other")["total"] == 2
    with pytest.raises(ServiceError) as e:
        pt.listing("c:CN", kind="nope")
    assert e.value.code == "INVALID_FACET_ID"


def test_summary_gives_distributions_and_the_object_itself(world):
    s = pt.summary("c:CN")
    assert (
        s["kind"] == "scope" and s["name"] == "中国" and s["indexed"] == 3
    )  # 3 个已接入（含 2 个仅目录来源）
    assert {t["name"]: t["count"] for t in s["tiers"]} == {"国家级政府": 2, "地方政府": 1}
    root = pt.summary(None)
    assert root["name"] == "全部门户" and root["total"] >= 28
    p = pt.summary("p:datax")
    assert p["kind"] == "p" and p["portal"]["level"] == "indexed_and_fetchable"
    assert [x["id"] for x in p["path"]] == ["c:CN", "p:datax"]
    k = pt.summary("k:source:nbs")
    assert k["kind"] == "k" and k["path"][-1]["id"] == "k:source:nbs"


def test_other_axes_group_by_kind_or_tier_then_country_then_portals(world):
    top = {c["id"]: c for c in pt.children(axis="kind")["children"]}
    assert top["s:kind=statistics"]["count"] == 1 and top["s:kind=other"]["name"] == "其他"
    countries = pt.children("s:kind=other")["children"]
    assert any(c["id"] == "s:kind=other;country=US" and c["count"] == 24 for c in countries)
    portals = pt.children("s:kind=other;country=US", limit=10)
    assert portals["total_children"] == 24 and len(portals["children"]) == 10
    assert pt.listing("s:kind=other;country=US")["total"] == 24
    assert [x["name"] for x in pt.summary("s:kind=other;country=US")["path"]] == ["其他", "美国"]
    tiers = pt.children(axis="tier")["children"]
    assert tiers[0]["id"].startswith("s:tier=") and sum(t["count"] for t in tiers) == 28
    with pytest.raises(ServiceError):
        pt.children(axis="zzz")
    with pytest.raises(ServiceError) as e:
        pt.children("s:bogus=1")
    assert e.value.code == "NOT_FOUND"
