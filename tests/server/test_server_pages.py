import json

import pytest
from fastapi.testclient import TestClient

from datafinder.models import Dataset
from datafinder.portals.sync import Portal, write_db
from datafinder.server import app as server_app
from datafinder.sources.base import Source, SourceSpec
from datafinder.store import connect


@pytest.fixture()
def client():
    return TestClient(server_app.create_app(), base_url="http://127.0.0.1:8787")


def P(id, name, **kw):
    kw.setdefault("source", "dateno")
    kw.setdefault("url", f"https://{id}.example.org")
    return Portal(id=id, name=name, **kw)


@pytest.fixture()
def db():
    conn = connect()
    write_db(
        conn,
        [
            P(
                "nbs",
                "NBS",
                url="https://data.stats.gov.cn",
                name_zh="国家统计局",
                country="CN",
                catalog_type="Indicators catalog",
                owner_type="Central government",
                industries=["statistics"],
                featured=True,
                is_national=True,
            ),
            P(
                "bj",
                "Beijing Stats",
                name_zh="北京市统计局",
                country="CN",
                region="CN-BJ",
                catalog_type="Indicators catalog",
                owner_type="Regional government",
                industries=["statistics"],
                labels=["stats_bureau"],
                verify="ok",
            ),
            P(
                "sh",
                "Shanghai Stats",
                name_zh="上海市统计局",
                country="CN",
                region="CN-SH",
                catalog_type="Indicators catalog",
                owner_type="Regional government",
                industries=["statistics"],
                labels=["stats_bureau"],
                verify="unverified",
                note="未能核验",
            ),
            P(
                "gdp",
                "Shanghai Open Data",
                country="CN",
                region="CN-SH",
                catalog_type="Open data portal",
                owner_type="Local government",
                industries=["government"],
                labels=["open_platform"],
                note="需注册",
                has_api=True,
            ),
            P(
                "wb",
                "World Bank",
                country="World",
                catalog_type="Indicators catalog",
                owner_type="International",
                industries=["economy", "statistics"],
            ),
        ],
        {"registry_ref": "test"},
    )
    conn.close()


# ───────────── 分类浏览 ─────────────


def test_browse_facets_and_portals(client, db):
    fx = client.get("/api/browse/facets").json()
    assert fx["synced"] and fx["total"] == 5
    assert {d["id"]: d["count"] for d in fx["domains"]}["statistics"] == 4
    assert {c["id"]: c["count"] for c in fx["countries"]} == {"CN": 4, "World": 1}

    r = client.get("/api/browse/portals?country=CN&domain=statistics&sort=official").json()
    assert r["total"] == 3 and r["portals"][0]["name"] == "国家统计局"  # 精选 + 国家级排第一
    assert r["portals"][0]["org_type"] == "国家级政府" and r["portals"][0]["official"] is True

    r = client.get("/api/browse/portals?tiers=1").json()
    assert [p["name"] for p in r["portals"]] == ["World Bank"]
    r = client.get("/api/browse/portals?official=true&api=true").json()
    assert [p["id"] for p in r["portals"]] == ["gdp"]


def test_browse_pagination_and_validation(client, db):
    r = client.get("/api/browse/portals?size=2&page=2").json()
    assert r["total"] == 5 and len(r["portals"]) == 2 and r["page"] == 2
    assert client.get("/api/browse/portals?size=999").status_code == 422
    assert client.get("/api/browse/portals?sort=evil").status_code == 422
    assert client.get("/api/browse/portals?tiers=a,b").status_code == 400
    assert client.get("/api/browse/portals?page=0").status_code == 422


def test_browse_before_sync_says_so(client):
    assert client.get("/api/browse/facets").json() == {"synced": False}
    assert client.get("/api/browse/portals").json()["synced"] is False


def test_browse_filter_values_are_parameterized_not_interpolated(client, db):
    evil = "x' OR 1=1 --"
    r = client.get("/api/browse/portals", params={"domain": evil, "kind": evil, "label": evil})
    assert r.status_code == 200 and r.json()["total"] == 0  # 当作普通字符串，什么也匹配不到


# ───────────── 检索入口 ─────────────


def test_links_endpoint_uses_translated_query(client):
    data = client.get("/api/links", params={"q": "人口普查"}).json()
    flat = {link["id"]: link for g in data["groups"] for link in g["links"]}
    assert "population%20census" in flat["dateno"]["url"] and data["query"] == "人口普查"
    assert client.get("/api/links?q=" + "x" * 300).status_code == 422


# ───────────── 搜索结果的详情增强 ─────────────


class Fake(Source):
    def __init__(self, id, items):
        super().__init__(SourceSpec(id=id, title=id, protocol="fake"))
        self.items = items

    def cache_query(self, plan):
        return plan.raw

    async def search(self, ctx, plan, limit):
        return [Dataset.from_dict(d.to_dict()) for d in self.items]


def test_search_snapshot_carries_region_highlights_and_links(client, db, monkeypatch):
    nbs_item = Dataset(
        "国内生产总值 (亿元)",
        "https://data.stats.gov.cn/dg/website/page.html?cid=1&indicatorId=2",
        "nbs",
        landing_url="https://data.stats.gov.cn/dg/website/page.html",
        description="国家统计局年度数据。全国，最新 2025年：1401879.2",
        publisher="国家统计局",
        period="2025年",
    )
    zen = Dataset(
        "GDP and census tables of Japan",
        "https://zenodo.org/records/9",
        "zenodo",
        description="Census tables",
        doi="10.5281/zenodo.9",
        license="cc-by-4.0",
    )
    monkeypatch.setattr(
        "datafinder.finder.build_sources",
        lambda **kw: [Fake("nbs", [nbs_item]), Fake("zenodo", [zen])],
    )
    events = []
    with client.stream("GET", "/api/search?q=国内生产总值") as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    last = [e for e in events if e["type"] == "snapshot"][-1]
    by_title = {d["title"]: d for d in last["datasets"]}

    n = by_title["国内生产总值 (亿元)"]
    assert n["region"] == "中国" and n["region_basis"] == "来源所在地区"
    assert n["open_url"] == "https://data.stats.gov.cn/dg/website/page.html"  # 给人点的是官网入口
    assert n["portal"]["name"] == "国家统计局" and n["portal"]["official"] is True
    labels = {h["label"]: h for h in n["highlights"]}
    assert labels["数据期"]["value"] == "2025年" and "国家级政府" in labels["发布方"]["value"]

    z = by_title["GDP and census tables of Japan"]
    assert z["region"] == "日本" and z["region_basis"] == "标题或描述中提到"
    assert z["doi_url"] == "https://doi.org/10.5281/zenodo.9" and z["open_url"] == z["url"]
    zl = {h["label"]: h for h in z["highlights"]}
    assert "推断" not in (zl["地区"]["note"] or "") and "标题或描述" in zl["地区"]["note"]


# ───────────── 资源目录接口 ─────────────


@pytest.fixture()
def catalog_ready():
    from datafinder.catalog import Catalog

    from ..catalog.conftest import build

    build(Catalog.open()).close()


def test_catalog_search_facets_and_inspect_endpoints(client, catalog_ready):
    r = client.get(
        "/api/catalog/search", params={"q": "工业增加值", "region": "河北", "kind": "indicator"}
    ).json()
    assert (
        r["results"][0]["resource_id"] == "nbs:indicator:6/c1/i1"
        and r["results"][0]["delivery"] == "generate"
    )
    assert r["facets"]["delivery"] and r["total"] >= 1
    f = client.get("/api/catalog/facets").json()
    assert any(t["id"] == "cnstat.industry" for t in f["topics"])
    d = client.get(
        "/api/catalog/inspect", params={"id": "nbs:dataset:6/c1", "children": True}
    ).json()
    assert [c["title"] for c in d["children"]] == ["工业增加值", "第二产业增加值"] and d[
        "how_to_get"
    ]["tool"] == "fetch"


def test_catalog_endpoints_translate_errors_to_http_statuses(client, catalog_ready):
    bad = client.get("/api/catalog/search", params={"q": "工业", "topic": "cnstat.nope"})
    assert bad.status_code == 400 and "INVALID_FACET_ID" in bad.json()["detail"]
    assert client.get("/api/catalog/inspect", params={"id": "nbs:nope"}).status_code == 404
    assert client.get("/api/catalog/search", params={"q": "x" * 300}).status_code == 422
    assert client.get("/api/catalog/search", params={"limit": 999}).status_code == 422


def test_catalog_search_without_catalog_is_503_with_next_step(client):
    r = client.get("/api/catalog/search", params={"q": "工业"})
    assert r.status_code == 503 and "index build" in r.json()["detail"]


def test_catalog_endpoints_never_download_or_write_files(client, catalog_ready):
    from datafinder.tables import exports_dir

    client.get("/api/catalog/search", params={"q": "工业增加值"})
    client.get("/api/catalog/inspect", params={"id": "yb.sd:table:2025/14-08"})
    assert not list(exports_dir().glob("*"))
    assert (
        client.get("/api/catalog/download").status_code == 404
    )  # 网页没有下载接口：获取只走命令行/MCP


def test_catalog_tree_endpoint_is_stateless_and_carries_filters(client, catalog_ready):
    top = client.get("/api/catalog/tree").json()
    assert [a["id"] for a in top["axes"]] == ["topic", "region", "source"] and top["total"] == 9
    t = client.get("/api/catalog/tree", params={"axis": "topic", "q": "工业增加值"}).json()
    ids = {c["id"] for c in t["children"]}
    assert "cnstat.industry" in ids and "cnstat.population" not in ids
    s = client.get("/api/catalog/tree", params={"axis": "source", "node": "source:nbs"}).json()
    assert s["path"][-1]["name"] == "国家统计局"
    # 选中节点后附带的参数可以直接用于检索：domain / source 参数
    r = client.get("/api/catalog/search", params={"source": "yb.sd", "kind": "table"}).json()
    assert r["total"] == 3 and all(x["source_id"] == "yb.sd" for x in r["results"])


def test_catalog_tree_errors_map_to_http_statuses(client, catalog_ready):
    assert client.get("/api/catalog/tree", params={"axis": "colour"}).status_code == 404
    assert (
        client.get("/api/catalog/tree", params={"axis": "topic", "node": "cnstat.nope"}).status_code
        == 404
    )
    bad = client.get("/api/catalog/tree", params={"axis": "topic", "topic": "cnstat.nope"})
    assert bad.status_code == 400 and "INVALID_FACET_ID" in bad.json()["detail"]
    assert client.get("/api/catalog/tree", params={"limit": 999}).status_code == 422


def test_data_sources_endpoints(client, catalog_ready):
    from datafinder.catalog import Catalog
    from datafinder.portals.sync import write_db
    from datafinder.store import connect

    from ..conftest import P

    cat = Catalog.open()
    cat.upsert_source(
        "nbs",
        domain_id="official_statistics_cn",
        adapter_id="nbs",
        name="国家统计局",
        base_url="https://data.example.org/",
    )
    cat.close()
    conn = connect()
    write_db(
        conn,
        [P("datax", "Data X", name_zh="国家数据", url="https://data.example.org", country="CN")],
        meta={},
    )
    conn.close()
    r = client.get("/api/data-sources").json()
    assert (
        r["items"][0]["name"] == "国家数据"
        and r["items"][0]["integration"]["level"] == "indexed_and_fetchable"
    )
    d = client.get("/api/data-sources/detail", params={"id": "datax"}).json()
    assert d["sources"][0]["source_id"] == "nbs"
    res = client.get(
        "/api/data-sources/resources", params={"id": "datax", "q": "工业增加值"}
    ).json()
    assert res["total"] >= 1 and all(x["source_id"] == "nbs" for x in res["results"])
    assert client.get("/api/data-sources/detail", params={"id": "nope"}).status_code == 404
    assert client.get("/api/data-sources", params={"scope": "x"}).status_code == 400
    assert client.get("/api/data-sources", params={"size": 999}).status_code == 422
