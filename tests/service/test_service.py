import json
from pathlib import Path

import pytest

from datafinder import service
from datafinder.catalog import Catalog
from datafinder.catalog.adapters import Adapter, register_adapter
from datafinder.service import ServiceError
from datafinder.tables import Table, exports_dir

pytestmark = pytest.mark.usefixtures("ready")

IND = "nbs:indicator:6/c1/i1"
TABLE_JPG = "yb.sd:table:2025/14-08"
SERIES = "yb.sd:series:CN-SD"


# ───────────── 目录没建好 ─────────────


async def test_empty_catalog_gives_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFINDER_HOME", str(tmp_path / "empty"))
    with pytest.raises(ServiceError) as e:
        await service.search("工业")
    assert e.value.code == "INDEX_NOT_READY" and "index build" in str(e.value)
    assert e.value.to_dict()["error"]["next_action"] == "datafinder index pull"


# ───────────── 检索 / 分面 / 来源 ─────────────


async def test_search_returns_catalog_results_portals_and_never_downloads(portals):
    r = await service.search("工业增加值", region_ids=["河北"], kinds_=["indicator"])
    assert r["results"][0]["resource_id"] == IND and r["results"][0]["delivery"] == "generate"
    assert r["is_complete"] and "facets" in r and isinstance(r["portals"], list)
    assert not list(exports_dir().glob("*"))  # 检索不会产生任何文件


async def test_search_translates_invalid_facets_to_coded_errors():
    with pytest.raises(ServiceError) as e:
        await service.search("工业", topic_ids=["cnstat.nope"])
    assert e.value.code == "INVALID_FACET_ID" and "facets" in e.value.next_action


async def test_portals_are_listed_separately_and_filtered_by_region(portals):
    r = await service.search("统计局", region_ids=["CN-HE"])
    names = [p["title"] for p in r["portals"]]
    assert "河北省统计局" in names and all(p["delivery"] == "link" for p in r["portals"])
    assert r["portals"] == [] or all(p["region"] in (None, "河北") for p in r["portals"])


def test_facets_vocabulary_has_topic_tree_regions_kinds_and_delivery():
    f = service.facets()
    ids = {t["id"]: t for t in f["topics"]}
    assert ids["cnstat.industry"]["count"] >= 3 and ids["cnstat.industry.value_added"]["level"] == 2
    assert "cnstat.quality" not in ids  # 预留主题不出现在分面里
    assert {"indicator", "table", "series"} <= {k["id"] for k in f["kinds"]} and "category" not in {
        k["id"] for k in f["kinds"]
    }
    assert {d["id"] for d in f["delivery"]} == {"download", "generate", "link"}
    assert service.facets("no_such_domain")["topics"] == []


def test_sources_and_status_report_capabilities_and_hints(portals):
    s = service.sources()
    assert {c["source_id"] for c in s["catalog_sources"]} == {"nbs", "yb.sd"} and "zenodo" in {
        x["id"] for x in s["live_sources"]
    }
    st = service.status()
    assert st["catalog"]["resources"] == 9 and st["portals"] == 3 and st["hints"] == []


# ───────────── 检视 ─────────────


def test_inspect_shows_parents_children_distributions_and_how_to_get():
    ind = service.inspect(IND)
    assert [p["title"] for p in ind["parents"]] == ["分省年度数据", "地区生产总值"]
    assert ind["how_to_get"]["tool"] == "fetch" and ind["how_to_get"]["needs"] == ["regions"]
    ds = service.inspect("nbs:dataset:6/c1")
    assert ds["how_to_get"]["needs"] == ["indicators", "regions"] and [
        c["title"] for c in ds["children"]
    ] == ["工业增加值", "第二产业增加值"]
    t = service.inspect(TABLE_JPG)
    assert t["how_to_get"]["tool"] == "download" and t["distributions"][0]["usability"] == "image"
    assert service.inspect(SERIES)["how_to_get"]["tool"] is None  # 只有链接


def test_inspect_unknown_resource_is_not_found():
    with pytest.raises(ServiceError) as e:
        service.inspect("nbs:indicator:nope")
    assert e.value.code == "NOT_FOUND"


# ───────────── 获取：默认只预览，confirm 才执行 ─────────────


class FakeAdapter(Adapter):
    id = "fake-nbs"
    domain_id = "official_statistics_cn"
    capabilities = frozenset({"fetch"})
    calls: list = []

    async def fetch(self, cat, entry, params):
        FakeAdapter.calls.append((entry.resource_id, params))
        rows = [
            ["2023年", "2023YY", "河北省", "工业增加值", 100.0, "亿元"],
            ["2024年", "2024YY", "河北省", "工业增加值", 110.0, "亿元"],
            ["2024年", "2024YY", "北京市", "工业增加值", 50.0, "亿元"],
        ]
        return Table(
            "工业增加值（河北省、北京市）",
            rows,
            meta={
                "网址": "https://x/p",
                "expected": {"regions": ["河北省", "北京市"], "indicators": ["工业增加值"]},
            },
        )


@pytest.fixture
def fake_adapter(monkeypatch):
    FakeAdapter.calls = []
    register_adapter(FakeAdapter())
    with Catalog.open() as cat:
        cat.conn.execute("UPDATE sources SET adapter_id='fake-nbs' WHERE source_id='nbs'")
        cat.conn.commit()
    return FakeAdapter


async def test_fetch_without_confirm_only_previews_and_touches_nothing(fake_adapter):
    plan = await service.fetch(IND, regions=["河北"])
    assert (
        plan["status"] == "plan" and plan["confirm_required"] and "confirm" in plan["next_action"]
    )
    assert fake_adapter.calls == [] and not list(exports_dir().glob("*"))


async def test_fetch_with_confirm_writes_file_manifest_stats_and_coverage(fake_adapter):
    r = await service.fetch(IND, regions=["河北", "北京"], start="2023", end="2024", confirm=True)
    saved = Path(r["saved_to"])
    assert (
        r["status"] == "done"
        and saved.exists()
        and saved.parent == exports_dir()
        and r["row_count"] == 3
    )
    m = json.loads(Path(str(saved) + ".manifest.json").read_text(encoding="utf-8"))
    assert (
        m["resource_id"] == IND
        and m["kind"] == "generated"
        and len(m["sha256"]) == 64
        and m["request"]["regions"] == ["河北", "北京"]
    )
    cov = r["coverage"]
    assert (
        cov["period_basis"] == "explicit"
        and cov["expected_cells"] == 4
        and cov["missing_cells"] == 1
    )
    assert (
        cov["missing_sample"]
        == [{"region": "北京市", "indicator": "工业增加值", "period": "2023YY"}]
        and cov["status"] == "partial"
    )
    assert r["describe"]["series"]  # 描述统计一并给出
    assert fake_adapter.calls[0][1].start == "2023"


async def test_fetch_rejects_resources_that_are_not_generated():
    for rid, hint in ((TABLE_JPG, "download"), (SERIES, "只有链接")):
        with pytest.raises(ServiceError) as e:
            await service.fetch(rid, confirm=True)
        assert e.value.code == "UNSUPPORTED" and hint in str(e.value)


async def test_untrusted_callers_cannot_write_outside_exports_dir(fake_adapter, tmp_path):
    with pytest.raises(ServiceError, match="只能写入导出目录"):
        await service.fetch(IND, regions=["河北"], path=str(tmp_path / "x.csv"), confirm=True)
    ok = await service.fetch(
        IND, regions=["河北"], path=str(tmp_path / "mine.csv"), confirm=True, trusted=True
    )
    assert Path(ok["saved_to"]) == tmp_path / "mine.csv"


def test_download_preview_names_format_usability_and_other_options():
    p = service.download(TABLE_JPG)
    assert (
        p["status"] == "plan"
        and p["file_format"] == "jpg"
        and p["usability"] == "image"
        and "OCR" in p["warning"]
    )
    assert p["confirm_required"] and p["license"].startswith("许可未知")


def test_download_with_confirm_saves_file_and_manifest(monkeypatch):
    def fake(url, dest_dir, *, hint, transport=None):
        dest_dir.mkdir(parents=True, exist_ok=True)
        f = dest_dir / "14-08.jpg"
        f.write_bytes(b"\xff\xd8fake")
        return f, 6

    monkeypatch.setattr("datafinder.service.acquire.download_file", fake)
    r = service.download(TABLE_JPG, confirm=True)
    saved = Path(r["saved_to"])
    assert (
        r["status"] == "done"
        and r["size_bytes"] == 6
        and saved.parent == exports_dir() / "downloads"
    )
    m = json.loads(Path(str(saved) + ".manifest.json").read_text(encoding="utf-8"))
    assert (
        m["kind"] == "downloaded"
        and m["usability"] == "image"
        and m["source_url"].endswith("14-08.jpg")
    )


def test_download_unavailable_and_bad_index_are_coded_errors():
    with pytest.raises(ServiceError) as e:
        service.download(IND)
    assert e.value.code == "DOWNLOAD_UNAVAILABLE"
    with pytest.raises(ServiceError) as e:
        service.download(TABLE_JPG, distribution_index=5)
    assert e.value.code == "INVALID_ARGUMENT"


async def test_get_dispatches_by_delivery(fake_adapter):
    assert (await service.get(SERIES))["status"] == "link"
    assert (await service.get(TABLE_JPG))["delivery"] == "download"
    assert (await service.get(IND, regions=["河北"]))["delivery"] == "generate"


# ───────────── 找数方案 / 索引 ─────────────


def test_plan_sources_groups_by_kind_with_fetch_hints_and_no_domain_words(portals):
    plan = service.plan_sources("河北省工业增加值", None)
    u = plan["understanding"]
    assert u["region_codes"] == ["CN-HE"] and u["country"] == "CN"
    ind = {i["resource_id"]: i for i in plan["sources"]["indicators"]["items"]}
    assert ind[IND]["fetch"] == {"tool": "fetch", "args": {"resource_id": IND, "regions": ["河北"]}}
    assert "nbs:indicator:3/c9/i1" in ind  # 全国口径也给（做地区研究要和全国对照）
    assert (
        plan["sources"]["tables"]["items"] == []
    )  # 目录里没有河北的年鉴表：如实为空，不拿山东的冒充
    assert plan["analysis_hints"] and plan["caveats"]
    assert "河北省统计局" in [p["name"] for p in plan["sources"]["regional_portals"]["items"]]
    sd = service.plan_sources("山东省规模以上工业增加值")["sources"]["tables"]["items"]
    assert sd and sd[0]["resource_id"] == TABLE_JPG and sd[0]["versions"] == [2025, 2024]


def test_plan_merges_catalog_agency_portals_with_the_portal_db_without_duplicates(portals):
    from datafinder.catalog import Distribution, Entry, RegionLink

    with Catalog.open() as cat:
        cat.upsert_source(
            "site.pbc", domain_id="official_statistics_cn", adapter_id="site", name="人民银行网站"
        )
        cn = [RegionLink("CN", "covers", None, "exact", "structure")]
        cat.upsert(
            [
                Entry(
                    "site.pbc",
                    "portal",
                    "home",
                    "中国人民银行网站",
                    path="货币政策 金融统计 社会融资规模 货币供应量",
                    landing_url="https://pbc.example.gov/",
                    access_status="blocked",
                    regions=cn,
                    distributions=[Distribution("manual", "https://pbc.example.gov/")],
                ),
                Entry(  # 与门户库里的"工业和信息化部"是同一个网站：只应出现一次
                    "site.pbc",
                    "portal",
                    "miit",
                    "工业和信息化部网站",
                    path="工业 制造业",
                    landing_url="https://miit.example.org/",
                    regions=cn,
                    distributions=[Distribution("page", "https://miit.example.org/")],
                ),
            ]
        )
    items = service.plan_sources("社会融资规模与货币供应量")["sources"]["official_portals"]["items"]
    pbc = next(p for p in items if p["name"] == "中国人民银行网站")
    assert (
        pbc["source"] == "catalog" and pbc["verify"] == "blocked"
    )  # 目录里的机构入口，标明来源与核验状态
    urls = [
        p["url"].rstrip("/")
        for p in service.plan_sources("工业增加值")["sources"]["official_portals"]["items"]
    ]
    assert len(urls) == len(set(urls))  # 同一网站不重复


def test_plan_sources_validates_input():
    with pytest.raises(ServiceError) as e:
        service.plan_sources("  ")
    assert e.value.code == "INVALID_ARGUMENT"


async def test_index_build_validates_adapter_params_with_coded_error():
    with pytest.raises(ServiceError) as e:
        await service.index.build(["nbs"], params={"db": "99"})
    assert e.value.code == "INVALID_ARGUMENT" and "未知数据库代码" in str(e.value)
    with pytest.raises(ServiceError) as e:
        await service.index.build(["nope"])
    assert e.value.code == "UNSUPPORTED"


def test_index_migrate_without_legacy_reports_not_migrated():
    out = service.index.migrate()
    assert {o["adapter"] for o in out} == {"nbs", "yearbook"} and all(
        o["migrated"] is False for o in out
    )


async def test_tables_with_both_a_file_and_a_parseable_web_page_can_be_fetched_too(fake_adapter):
    from datafinder.catalog import Distribution, Entry

    with Catalog.open() as cat:
        cat.conn.execute("UPDATE sources SET adapter_id='fake-nbs' WHERE source_id='yb.sd'")
        cat.conn.commit()
        cat.upsert(
            [
                Entry(
                    "yb.sd",
                    "table",
                    "2025/99-1",
                    "各地区人口",
                    distributions=[
                        Distribution("direct_file", "https://x/a.xls", "xls", "tabular"),
                        Distribution("html_parse", "https://x/a.htm", "html", "html_table"),
                    ],
                )
            ]
        )
    rid = "yb.sd:table:2025/99-1"
    assert (await service.get(rid))["delivery"] == "download"  # 自动分流：有 xls 先给下载
    r = await service.fetch(rid, confirm=True)  # 但也可以明确要求把网页表解析成整洁表
    assert r["status"] == "done" and fake_adapter.calls[-1][0] == rid
    how = service.inspect(rid)["how_to_get"]
    assert how["tool"] == "download" and how["also"]["tool"] == "fetch"
    # 只有文件、没有可生成入口的资源仍然不能 fetch
    with pytest.raises(ServiceError, match="不能 fetch"):
        await service.fetch("yb.sd:table:2025/14-08", confirm=True)


# ───────────── 术语词典 ─────────────


def test_glossary_list_detail_and_find_data_come_with_source_and_status():
    r = service.glossary.list_terms("GDP")
    assert r["items"][0]["name_zh"] == "国内生产总值" and r["items"][0]["status_zh"] == "官方原文"
    assert r["topics"] and r["items"][0]["source_url"].startswith(
        "https://www.stats.gov.cn/sj/zbjs/"
    )
    d = service.glossary.detail(r["items"][0]["term_id"])
    assert d["definition"].startswith("指一个国家所有常住单位")  # 官方原文，不改写
    assert any(
        x["type"] == "not_equivalent" and x["name_zh"] == "国民总收入" for x in d["relations"]
    )
    assert d["find_data"] == {"query": "国内生产总值", "topic_ids": ["cnstat.macro"]}
    assert d["source_resource_id"] is None  # 出处页面不在这个（测试）目录里
    with pytest.raises(ServiceError) as e:
        service.glossary.detail("nope")
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(ServiceError) as e:
        service.glossary.list_terms(topic="cnstat.nope")
    assert e.value.code == "INVALID_FACET_ID"


def test_inspect_and_plan_carry_official_definitions_only_for_exact_term_names(portals):
    cat = Catalog.open()
    cat.upsert_source(
        "nbs", domain_id="official_statistics_cn", adapter_id="nbs", name="国家统计局"
    )
    from datafinder.catalog import Entry

    cat.upsert(
        [
            Entry("nbs", "indicator", "x/1", "国内生产总值 (亿元)"),
            Entry("nbs", "indicator", "x/2", "工业增加值"),
        ]
    )
    cat.close()
    gdp = service.inspect("nbs:indicator:x/1")["glossary"]
    assert gdp and gdp[0]["name_zh"] == "国内生产总值" and gdp[0]["source_url"]
    assert service.inspect("nbs:indicator:x/2")["glossary"] == []  # 词典里没有的就不说
    plan = service.plan_sources("河北省国内生产总值", "河北")
    assert [t["name_zh"] for t in plan["sources"]["glossary"]["items"]] == ["国内生产总值"]


def test_glossary_detail_links_to_the_catalog_resource_of_its_source_page():
    from datafinder.catalog import Entry

    d0 = service.glossary.list_terms("国内生产总值")["items"][0]
    cat = Catalog.open()
    cat.upsert_source(
        "site.nbs", domain_id="official_statistics_cn", adapter_id="site", name="官网"
    )
    cat.upsert(
        [Entry("site.nbs", "reference", "zbjs", "国民经济核算", landing_url=d0["source_url"])]
    )
    cat.close()
    assert service.glossary.detail(d0["term_id"])["source_resource_id"] == "site.nbs:reference:zbjs"
