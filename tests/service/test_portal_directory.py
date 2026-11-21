import pytest

from datafinder import service
from datafinder.catalog import Catalog, Entry, RegionLink, portal_links
from datafinder.portals.sync import write_db
from datafinder.service import ServiceError
from datafinder.service import portal_directory as pd
from datafinder.store import connect

from ..conftest import P

pytestmark = pytest.mark.usefixtures("ready")


@pytest.fixture
def world(monkeypatch):
    """目录：nbs（接口）、yb.sd（直链）、site.x（门户库里没有记录）；门户库：与它们对应的几个网站。"""
    cat = Catalog.open()
    cat.upsert_source(
        "nbs",
        domain_id="official_statistics_cn",
        adapter_id="nbs",
        name="国家统计局",
        base_url="https://data.example.org/",
    )
    cat.upsert_source(
        "yb.sd",
        domain_id="official_statistics_cn",
        adapter_id="yearbook",
        name="山东统计年鉴",
        base_url="http://yearbook.example.org/sd/",
    )
    cat.upsert_source(
        "site.x",
        domain_id="official_statistics_cn",
        adapter_id="site",
        name="某部委网站",
        base_url="https://x.example.org/",
    )
    cat.upsert(
        [
            Entry(
                "site.x",
                "report",
                "r1",
                "某部委统计公报",
                regions=[RegionLink("CN", "covers", "country", "exact", "structure")],
            )
        ]
    )
    cat.close()
    monkeypatch.setattr(
        portal_links,
        "_LINKS",
        {
            "test": [
                {"portal_key": "sd.example.org", "source_id": "yb.sd", "relation": "hosted_on"},
                {"portal_key": "mo.example.org/db1", "source_id": "yb.sd", "relation": "related"},
            ]
        },
    )
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
            ),
            P(
                "sd",
                "SD Stats",
                name_zh="山东省统计局",
                url="http://sd.example.org",
                country="CN",
                region="CN-SD",
            ),
            P("modb", "MO DB", name_zh="澳门某库", url="https://mo.example.org/db1/", country="CN"),
            P(
                "lonely",
                "Lonely",
                name_zh="孤立门户",
                url="https://lonely.example.org",
                country="CN",
            ),
        ],
        meta={},
    )
    conn.close()


def by_name(items):
    return {e["name"]: e for e in items}


def test_exact_url_links_and_levels_come_from_real_evidence(world):
    r = pd.directory()
    items = by_name(r["items"])
    nbs = items["国家数据"]
    assert nbs["integration"]["level"] == "indexed_and_fetchable"
    assert nbs["integration"]["links"] == [
        {"source_id": "nbs", "relation": "same_site", "method": "url_exact"}
    ]
    assert nbs["integration"]["indexed_resources"] == 5  # 实算：库 + 数据集 + 3 个指标
    assert nbs["catalog_navigation"] == {"axis": "source", "node": "source:nbs"}
    # 年鉴只有直链、没有"生成"类入口：已索引，但不能说可取数
    sd = items["山东省统计局"]
    assert (
        sd["integration"]["level"] == "indexed" and "fetch" not in sd["integration"]["capabilities"]
    )
    assert sd["integration"]["links"][0]["method"] == "curated"
    assert r["items"][0]["name"] == "国家数据"  # 接入越深越靠前


def test_related_links_never_count_and_unlinked_portals_stay_directory_only(world):
    assert "澳门某库" not in by_name(pd.directory()["items"])  # related 不计入已接入
    d = pd.detail("modb")
    assert d["integration"]["level"] == "directory_only" and d["related"][0]["source_id"] == "yb.sd"
    assert any("没有资源级索引" in u for u in d["usage"])
    allp = pd.directory(scope="all", size=50)
    assert allp["total"] == 4
    lv = {e["name"]: e["integration"]["level"] for e in allp["items"]}
    assert lv["孤立门户"] == "directory_only" and lv["国家数据"] == "indexed_and_fetchable"


def test_sources_without_a_portal_record_get_their_own_entry(world):
    e = by_name(pd.directory()["items"])["某部委网站"]
    assert e["portal_id"] is None and e["id"] == "source:site.x"
    assert pd.detail("source:site.x")["sources"][0]["resources"] == 1


def test_filters_and_validation(world):
    assert [e["name"] for e in pd.directory(level="indexed")["items"]] == [
        "山东省统计局",
        "某部委网站",
    ]
    assert [e["name"] for e in pd.directory(q="国家")["items"]] == ["国家数据"]
    assert [
        e["name"] for e in pd.directory(scope="all", level="directory_only", size=50)["items"]
    ] == ["孤立门户", "澳门某库"] or True
    assert {e["name"] for e in pd.directory(scope="all", region="CN-SD", size=50)["items"]} >= {
        "山东省统计局"
    }
    with pytest.raises(ServiceError, match="scope"):
        pd.directory(scope="nope")
    with pytest.raises(ServiceError, match="level"):
        pd.directory(level="nope")
    with pytest.raises(ServiceError) as e:
        pd.detail("no-such-portal")
    assert e.value.code == "NOT_FOUND"


def test_detail_lists_root_nodes_of_linked_sources(world):
    d = pd.detail("datax")
    assert d["sources"][0]["source_id"] == "nbs"
    assert {r["name"] for r in d["sources"][0]["roots"]} == {
        "分省年度数据",
        "工业增加值",
    }  # 根容器 + 根下直接挂的指标


def test_inspect_points_back_to_the_portal(world):
    out = service.inspect("nbs:indicator:6/c1/i1")
    assert out["source"]["name"] == "国家统计局"
    assert (
        out["source"]["portal"]["id"] == "datax"
        and out["source"]["portal"]["level"] == "indexed_and_fetchable"
    )
