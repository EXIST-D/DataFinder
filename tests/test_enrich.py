import pytest

from datafinder.enrich import access_summary, detect_region, enrich, lookup_portal
from datafinder.models import Dataset, Resource
from datafinder.portals.sync import Portal, write_db
from datafinder.sources.base import SourceSpec
from datafinder.store import connect


def D(title="T", url="https://a.org/1", source="zenodo", **kw):
    return Dataset(title=title, url=url, source=source, **kw)


def spec(**opts):
    return SourceSpec(id="s", title="S", protocol="p", options=opts)


def test_region_from_source_default_beats_text():
    region, basis = detect_region(D("America data"), spec(country="CN"))
    assert region == "中国" and basis == "来源所在地区"


def test_region_detected_from_title_and_marked_as_inferred():
    region, basis = detect_region(D("Population census of China 2020"), spec())
    assert region == "中国" and basis == "标题或描述中提到"


def test_province_in_chinese_text():
    region, basis = detect_region(D("辽宁省2020年人口数据"), None)
    assert region == "中国·辽宁" and basis


def test_many_countries_collapse_to_multi_country():
    region, _ = detect_region(D("Data for China, Japan, Korea and India"), None)
    assert region == "多国"


def test_global_wording():
    assert detect_region(D("Global temperature records"), None)[0] == "全球/多国"


def test_unknown_region_is_not_invented():
    assert detect_region(D("Some dataset about widgets"), None) == (None, None)


def test_access_summary_cases():
    f = Resource("file", "a.csv", "https://x/a.csv")
    a = Resource("api", "api", "https://x/api")
    assert access_summary(D(resources=[f])).startswith("可直接下载")
    assert access_summary(D(resources=[a])).startswith("通过接口")
    assert "也提供接口" in access_summary(D(resources=[f, a]))
    assert "受限" in access_summary(D(license="restricted access", resources=[f]))
    assert "上游未列出" in access_summary(D())


@pytest.fixture()
def conn():
    c = connect(":memory:")
    write_db(
        c,
        [
            Portal(
                id="z",
                name="Zenodo",
                url="https://www.zenodo.org",
                source="dateno",
                country="CH",
                owner_type="Academy",
            ),
            Portal(
                id="nbs",
                name="NBS",
                name_zh="国家统计局·国家数据",
                url="https://data.stats.gov.cn",
                source="dateno",
                country="CN",
                owner_type="Central government",
            ),
        ],
        {},
    )
    return c


def test_portal_lookup_by_host(conn):
    p = lookup_portal(conn, "https://zenodo.org/records/1")
    assert p["name"] == "Zenodo" and p["org_type"] == "科研与高校" and p["official"] is False
    n = lookup_portal(conn, "https://data.stats.gov.cn/dg/website/page.html?cid=1")
    assert n["name"] == "国家统计局·国家数据" and n["official"] is True
    assert lookup_portal(conn, "https://doi.org/10.1/x") is None  # DOI 是跳转，不是所属门户
    assert lookup_portal(conn, "https://unknown.example.org/x") is None


def test_enrich_builds_highlights_without_inventing(conn):
    d = D(
        "Population of China 2020",
        url="https://zenodo.org/records/1",
        description="Census tables by province. Second sentence.",
        publisher="CAS",
        license="cc-by-4.0",
        updated="2025-01-02",
        doi="10.5281/zenodo.1",
        formats=["CSV", "XLSX"],
        size_bytes=2048,
        resources=[Resource("file", "a.csv", "https://z/a.csv", "CSV", 1024)],
        sources=["zenodo"],
    )
    e = enrich(d, spec=spec(), spec_titles={"zenodo": "Zenodo"}, portal=lookup_portal(conn, d.url))
    hl = {h["label"]: h for h in e.highlights}
    assert hl["简介"]["value"] == "Census tables by province."
    assert hl["地区"]["value"] == "中国" and "标题或描述" in hl["地区"]["note"]
    assert hl["发布方"]["value"] == "CAS（科研与高校）"
    assert hl["格式"]["value"] == "CSV、XLSX；约 2.0 KB"
    assert hl["许可"]["value"] == "cc-by-4.0" and hl["DOI"]["value"] == "10.5281/zenodo.1"
    assert e.doi_url == "https://doi.org/10.5281/zenodo.1" and e.open_url == d.url


def test_missing_fields_are_reported_as_unknown_not_filled(conn):
    e = enrich(D("Plain title"), spec=spec())
    hl = {h["label"]: h for h in e.highlights}
    assert hl["地区"]["value"] == "上游未标明" and hl["许可"]["value"].startswith("许可未知")
    assert "数据期" not in hl and "更新" not in hl and "DOI" not in hl and "格式" not in hl


def test_landing_url_is_used_for_the_open_link_when_set():
    d = D(
        url="https://data.stats.gov.cn/dg/website/page.html?cid=1&indicatorId=2",
        landing_url="https://data.stats.gov.cn/dg/website/page.html",
    )
    assert enrich(d).open_url == "https://data.stats.gov.cn/dg/website/page.html"
