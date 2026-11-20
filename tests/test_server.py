import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datafinder.engine.errors import SourceError
from datafinder.models import Dataset, Resource
from datafinder.portals.sync import Portal, write_db
from datafinder.server import app as server_app
from datafinder.sources.base import Source, SourceSpec
from datafinder.store import connect

STATIC = Path(server_app.__file__).parent / "static"


class Fake(Source):
    def __init__(self, id, items=None, error=None):
        super().__init__(SourceSpec(id=id, title=id, protocol="fake"))
        self.items = items or []
        self.error = error

    def cache_query(self, plan):
        return plan.raw

    async def search(self, ctx, plan, limit):
        if self.error:
            raise self.error
        return [Dataset(**{**d.to_dict(), "resources": list(d.resources)}) for d in self.items]


@pytest.fixture()
def client():
    return TestClient(server_app.create_app(), base_url="http://127.0.0.1:8787")


@pytest.fixture()
def portals_db():
    conn = connect()
    write_db(
        conn,
        [
            Portal(
                id="nbs",
                name="National Data NBS",
                name_zh="国家统计局·国家数据",
                url="https://data.stats.gov.cn",
                source="dateno",
                country="CN",
                catalog_type="Indicators catalog",
                industries=["statistics", "economy"],
                featured=True,
                is_national=True,
            ),
            Portal(
                id="jma",
                name="Japan Meteorological Agency",
                url="https://www.jma.go.jp",
                source="dateno",
                country="JP",
                catalog_type="Scientific data repository",
                industries=["climate"],
            ),
        ],
        {"registry_ref": "test"},
    )
    conn.close()


def patch_sources(monkeypatch, *sources):
    monkeypatch.setattr("datafinder.finder.build_sources", lambda **kw: list(sources))


def sse(client, url):
    events = []
    with client.stream("GET", url) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


# ───────────── 安全底线 ─────────────


def test_host_header_must_be_local(client):
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/status", headers={"host": "evil.example.com"}).status_code == 400
    assert client.get("/api/status", headers={"host": "localhost:8787"}).status_code == 200


def test_security_headers_and_strict_csp(client):
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/status").headers["cache-control"] == "no-store"


def test_static_files_comply_with_csp_and_stay_offline():
    for name in ("index.html", "app.js", "app.css"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert 'style="' not in text, f"{name} 有内联样式，会被 CSP 拦截"
        assert "onclick=" not in text and "<script>" not in text.replace("<script src", "")
        cleaned = text.replace("http://www.w3.org/2000/svg", "")
        assert "http://" not in cleaned and "https://" not in cleaned, f"{name} 引用了外部资源"


# ───────────── 页面与状态 ─────────────


def test_index_and_assets_served(client):
    html = client.get("/").text
    assert "DataFinder" in html and 'lang="zh-CN"' in html
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_status_reports_portal_count(client, portals_db):
    s = client.get("/api/status").json()
    assert s["portals"] == 2 and s["sources"] and "version" in s


# ───────────── 门户 ─────────────


def test_portals_when_not_synced(client):
    assert client.get("/api/portals?q=人口").json() == {"synced": False, "portals": []}


def test_portals_search_returns_chinese_display_fields(client, portals_db):
    data = client.get("/api/portals?q=统计局").json()
    assert data["synced"] and data["portals"][0]["name"] == "国家统计局·国家数据"
    p = data["portals"][0]
    assert p["country"] == "中国" and p["type"] == "统计指标库" and p["featured"]
    assert "统计" in p["industries"]


def test_portals_validates_input(client):
    assert client.get("/api/portals?limit=0").status_code == 422
    assert client.get("/api/portals?q=" + "x" * 300).status_code == 422


# ───────────── 数据集（SSE）─────────────


def test_search_streams_snapshots_progressively(client, monkeypatch):
    nbs = Fake(
        "nbs",
        [
            Dataset(
                "国内生产总值 (亿元)",
                "https://s.cn/?cid=1",
                "nbs",
                description="国家统计局年度数据",
                publisher="国家统计局",
            )
        ],
    )
    zen = Fake(
        "zenodo",
        [
            Dataset(
                "China GDP panel data",
                "https://zenodo.org/records/1",
                "zenodo",
                description="GDP by province",
                license="cc-by-4.0",
                resources=[Resource("file", "gdp.csv", "https://zenodo.org/f/gdp.csv", "CSV")],
            )
        ],
    )
    patch_sources(monkeypatch, nbs, zen)
    events = sse(client, "/api/search?q=国内生产总值")
    snaps = [e for e in events if e["type"] == "snapshot"]
    assert len(snaps) >= 3  # 初始 pending + 每个来源完成一次
    first, last = snaps[0], snaps[-1]
    assert [s["status"] for s in first["sources"]] == ["pending", "pending"] and not first[
        "datasets"
    ]
    assert not first["final"] and last["final"] and last["complete"]
    assert {d["title"] for d in last["datasets"]} == {"国内生产总值 (亿元)", "China GDP panel data"}
    nbs_src = next(s for s in last["sources"] if s["id"] == "nbs")
    assert nbs_src["experimental"] is True and nbs_src["count"] == 1
    assert "explain" not in last["datasets"][0]
    assert last["plan"]["zh_terms"] == ["国内生产总值"]


def test_failed_source_is_reported_not_hidden(client, monkeypatch):
    ok = Fake(
        "zenodo",
        [
            Dataset(
                "China GDP panel data",
                "https://zenodo.org/records/1",
                "zenodo",
                description="GDP data",
            )
        ],
    )
    bad = Fake("datacite", error=SourceError("down", "连接失败"))
    patch_sources(monkeypatch, ok, bad)
    last = [e for e in sse(client, "/api/search?q=gdp") if e["type"] == "snapshot"][-1]
    by_id = {s["id"]: s for s in last["sources"]}
    assert by_id["datacite"]["status"] == "error" and "连接失败" in by_id["datacite"]["message"]
    assert last["final"] and not last["complete"]
    assert len(last["datasets"]) == 1


def test_explain_flag_includes_ranking_breakdown(client, monkeypatch):
    patch_sources(
        monkeypatch,
        Fake("zenodo", [Dataset("GDP data", "https://z.org/1", "zenodo", description="gdp")]),
    )
    last = [e for e in sse(client, "/api/search?q=gdp&explain=true") if e["type"] == "snapshot"][-1]
    assert "覆盖度" in last["datasets"][0]["explain"]


def test_search_input_validation(client):
    assert client.get("/api/search").status_code == 422
    assert client.get("/api/search?q=").status_code == 422
    assert client.get("/api/search?q=" + "x" * 300).status_code == 422
    assert client.get("/api/search?q=gdp&limit=999").status_code == 422
    r = client.get("/api/search?q=gdp&sources=nope")
    assert r.status_code == 400 and "未知来源" in r.json()["detail"]


# ───────────── inspect：只允许搜到过的链接 ─────────────


def test_inspect_rejects_urls_never_returned_by_a_search(client):
    r = client.get("/api/inspect", params={"url": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 400


def test_inspect_allows_seen_url_and_maps_errors(client, monkeypatch):
    url = "https://zenodo.org/records/1"
    patch_sources(
        monkeypatch, Fake("zenodo", [Dataset("GDP data", url, "zenodo", description="gdp")])
    )
    sse(client, "/api/search?q=gdp")  # 搜索结果里出现过这个链接

    async def ok(u):
        return [Resource("file", "a.csv", "https://zenodo.org/f/a.csv", "CSV", 12)]

    monkeypatch.setattr(server_app, "inspect_url", ok)
    r = client.get("/api/inspect", params={"url": url})
    assert r.status_code == 200 and r.json()["resources"][0]["name"] == "a.csv"

    async def boom(u):
        raise SourceError("http", "读取文件列表失败：404")

    monkeypatch.setattr(server_app, "inspect_url", boom)
    r = client.get("/api/inspect", params={"url": url})
    assert r.status_code == 502 and "404" in r.json()["detail"]
