import json

import pytest
from fastapi.testclient import TestClient

from datafinder.engine.errors import SourceError
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


# ───────────── 中国统计专区 ─────────────


def test_china_overview(client, db):
    o = client.get("/api/china/overview").json()
    assert len(o["databases"]) == 13
    assert o["databases"][5] == {"code": "6", "name": "分省年度数据", "dt_type": "YY"}
    assert [b["name"] for b in o["bureaus"]] == [
        "北京市统计局",
        "上海市统计局",
    ]  # 按区划代码 11 → 31
    assert o["bureaus"][1]["verify"] == "unverified" and o["bureaus"][1]["note"] == "未能核验"
    assert [p["id"] for p in o["platforms"]] == ["gdp"] and o["platforms"][0]["note"] == "需注册"


# ───────────── 国家统计局代理接口 ─────────────

CID = "a" * 32
IND = "b" * 32
ROOT = "c" * 32


class FakeNbs:
    last_series = None

    def __init__(self, http, cache):
        pass

    async def tree(self, code, pid=""):
        return [
            {
                "id": CID,
                "name": "总人口",
                "is_leaf": True,
                "sdate": None,
                "edate": None,
                "root_id": ROOT,
            }
        ]

    async def indicators(self, cid):
        return [{"id": IND, "name": "年末常住人口 (万人)", "unit": "万人", "mark": "年末人口数"}]

    async def regions(self, cid):
        return {
            "catalogs": [],
            "regions": [
                {"text": "北京市", "value": "110000000000"},
                {"text": "上海市", "value": "310000000000"},
            ],
        }

    async def series(self, **kw):
        FakeNbs.last_series = kw
        return {
            "indicator": "年末常住人口",
            "unit": "万人",
            "areas": ["北京"],
            "periods": [{"code": "2023YY", "name": "2023年", "values": {"北京": "2186"}}],
        }


@pytest.fixture()
def fake_nbs(monkeypatch):
    monkeypatch.setattr(server_app, "NbsClient", FakeNbs)


def test_nbs_tree_indicators_regions(client, fake_nbs):
    t = client.get("/api/nbs/tree", params={"code": "6"}).json()
    assert t["nodes"][0]["name"] == "总人口"
    assert (
        client.get("/api/nbs/indicators", params={"cid": CID}).json()["indicators"][0]["unit"]
        == "万人"
    )
    assert (
        client.get("/api/nbs/regions", params={"cid": CID}).json()["regions"][0]["text"] == "北京市"
    )


def test_nbs_input_validation(client, fake_nbs):
    assert client.get("/api/nbs/tree", params={"code": "99"}).status_code == 400
    assert client.get("/api/nbs/tree", params={"code": "6", "pid": "../../x"}).status_code == 400
    assert client.get("/api/nbs/indicators", params={"cid": "short"}).status_code == 400
    base = {"code": "6", "cid": CID, "ind": IND, "root": ROOT}
    assert client.get("/api/nbs/series", params={**base, "areas": "北京"}).status_code == 400
    assert client.get("/api/nbs/series", params={**base, "areas": ""}).status_code == 400
    too_many = ",".join(f"{110000000000 + i}" for i in range(9))
    assert client.get("/api/nbs/series", params={**base, "areas": too_many}).status_code == 400
    r = client.get("/api/nbs/series", params={**base, "areas": "999999999999"})
    assert r.status_code == 400 and "不支持这些地区代码" in r.json()["detail"]


def test_nbs_series_resolves_region_names_server_side(client, fake_nbs):
    base = {
        "code": "6",
        "cid": CID,
        "ind": IND,
        "root": ROOT,
        "areas": "110000000000,310000000000",
        "n": 5,
    }
    r = client.get("/api/nbs/series", params=base)
    assert r.status_code == 200 and r.json()["periods"][0]["values"] == {"北京": "2186"}
    sent = FakeNbs.last_series
    # 地区名由服务端根据官方地区清单解析，不信任浏览器传来的文字
    assert sent["areas"] == [
        {"text": "北京市", "value": "110000000000"},
        {"text": "上海市", "value": "310000000000"},
    ]
    assert sent["n"] == 5 and sent["code"] == "6"


def test_nbs_rate_limit_becomes_429_with_a_clear_message(client, monkeypatch):
    class Limited(FakeNbs):
        async def tree(self, code, pid=""):
            raise SourceError(
                "rate_limited", "国家统计局接口暂时休息（站点防护要求人机验证），请稍后再试"
            )

    monkeypatch.setattr(server_app, "NbsClient", Limited)
    r = client.get("/api/nbs/tree", params={"code": "6"})
    assert r.status_code == 429 and "暂时休息" in r.json()["detail"]


def test_nbs_other_errors_become_502(client, monkeypatch):
    class Broken(FakeNbs):
        async def indicators(self, cid):
            raise SourceError("shape", "响应结构变了")

    monkeypatch.setattr(server_app, "NbsClient", Broken)
    assert client.get("/api/nbs/indicators", params={"cid": CID}).status_code == 502


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
