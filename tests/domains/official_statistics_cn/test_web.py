"""本领域的网页接口：国家统计局分类树/取值的代理、省级统计局与省市开放平台总览。"""

import pytest
from fastapi.testclient import TestClient

from datafinder.domains.official_statistics_cn import web
from datafinder.engine.errors import SourceError
from datafinder.portals.sync import Portal, write_db
from datafinder.server import app as server_app
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
            P("bj", "Beijing Stats", name_zh="北京市统计局", country="CN", region="CN-BJ", catalog_type="Indicators catalog",
              owner_type="Regional government", industries=["statistics"], labels=["stats_bureau"], verify="ok"),
            P("sh", "Shanghai Stats", name_zh="上海市统计局", country="CN", region="CN-SH", catalog_type="Indicators catalog",
              owner_type="Regional government", industries=["statistics"], labels=["stats_bureau"], verify="unverified",
              note="未能核验"),
            P("gdp", "Shanghai Open Data", country="CN", region="CN-SH", catalog_type="Open data portal",
              owner_type="Local government", industries=["government"], labels=["open_platform"], note="需注册", has_api=True),
        ],
        {"registry_ref": "test"},
    )
    conn.close()


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
    monkeypatch.setattr(web, "NbsClient", FakeNbs)


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

    monkeypatch.setattr(web, "NbsClient", Limited)
    r = client.get("/api/nbs/tree", params={"code": "6"})
    assert r.status_code == 429 and "暂时休息" in r.json()["detail"]


def test_nbs_other_errors_become_502(client, monkeypatch):
    class Broken(FakeNbs):
        async def indicators(self, cid):
            raise SourceError("shape", "响应结构变了")

    monkeypatch.setattr(web, "NbsClient", Broken)
    assert client.get("/api/nbs/indicators", params={"cid": CID}).status_code == 502


