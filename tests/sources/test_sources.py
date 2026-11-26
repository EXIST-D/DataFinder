import json
from pathlib import Path

import httpx
import pytest

from datafinder.domains.official_statistics_cn.nbs.search import NbsSource
from datafinder.domains.official_statistics_cn.nbs.search import to_dataset as nbs_to_dataset
from datafinder.engine.errors import ShapeError, SourceError
from datafinder.engine.http import Http
from datafinder.query.plan import parse
from datafinder.sources.base import Ctx, SourceSpec
from datafinder.sources.ckan import CkanSource
from datafinder.sources.datacite import DataCiteSource
from datafinder.sources.dataseek import DataseekSource
from datafinder.sources.registry import build_sources, load_specs
from datafinder.sources.zenodo import ZenodoSource

FIX = Path(__file__).parents[1] / "fixtures"


def fixture(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def ctx_for(payload, status=200, capture=None):
    def handler(req: httpx.Request):
        if capture is not None:
            capture.append(req)
        return httpx.Response(status, json=payload)

    return Ctx(Http(httpx.AsyncClient(transport=httpx.MockTransport(handler))))


def spec(id, protocol, **kw):
    return SourceSpec(id=id, title=id, protocol=protocol, **kw)


# ───────────── Zenodo ─────────────


async def test_zenodo_parses_real_response():
    seen = []
    ctx = ctx_for(fixture("zenodo.json"), capture=seen)
    items = await ZenodoSource(spec("zenodo", "zenodo")).search(ctx, parse("china population"), 10)
    assert items and all(d.url.startswith("https://zenodo.org/") for d in items)
    d = items[0]
    assert d.license == "cc-by-4.0" and d.doi and d.source == "zenodo"
    assert d.resources and d.resources[0].kind == "file" and d.resources[0].url.startswith("http")
    assert d.formats  # 由文件扩展名推出
    q = seen[0].url.params
    assert q["type"] == "dataset" and int(q["size"]) <= 25  # 匿名最多 25，超了会 400


async def test_zenodo_shape_error_when_hits_missing():
    ctx = ctx_for({"unexpected": True})
    with pytest.raises(ShapeError):
        await ZenodoSource(spec("zenodo", "zenodo")).search(ctx, parse("population"), 10)


# ───────────── DataCite ─────────────


async def test_datacite_parses_and_requests_relevance_sort():
    seen = []
    ctx = ctx_for(fixture("datacite.json"), capture=seen)
    items = await DataCiteSource(spec("datacite", "datacite")).search(ctx, parse("population"), 10)
    assert items and items[0].doi and items[0].url.startswith("http")
    assert items[0].period is None  # publicationYear 不是数据覆盖期，不能填进 period
    assert seen[0].url.params["sort"] == "relevance"
    assert seen[0].url.params["resource-type-id"] == "dataset"


# ───────────── CKAN ─────────────

HDX = spec(
    "hdx",
    "ckan",
    base_url="https://data.humdata.org/api/3/action",
    options={"url_pattern": "https://data.humdata.org/dataset/{name}"},
)
CANADA = spec(
    "open-canada",
    "ckan",
    base_url="https://open.canada.ca/data/api/action",
    options={"url_pattern": "https://open.canada.ca/data/en/dataset/{id}"},
)


async def test_ckan_parses_hdx_with_resources_and_formats():
    ctx = ctx_for(fixture("ckan_hdx.json"))
    items = await CkanSource(HDX).search(ctx, parse("china population"), 10)
    assert items
    d = items[0]
    assert d.url.startswith("https://data.humdata.org/dataset/")
    assert d.resources and d.formats == sorted(set(d.formats), key=d.formats.index)
    assert all(r.url.startswith("http") for r in d.resources)


async def test_ckan_reads_translated_title_when_title_is_empty():
    payload = fixture("ckan_canada.json")
    payload["result"]["results"][0]["title"] = ""
    items = await CkanSource(CANADA).search(ctx_for(payload), parse("population"), 5)
    assert items[0].title.startswith("Farm population")


async def test_ckan_success_false_is_error_not_empty_result():
    ctx = ctx_for({"success": False, "error": {"message": "Search error"}})
    with pytest.raises(SourceError) as e:
        await CkanSource(HDX).search(ctx, parse("population"), 5)
    assert "success=false" in str(e.value)


async def test_ckan_strips_colons_from_query():
    seen = []
    ctx = ctx_for(fixture("ckan_hdx.json"), capture=seen)
    await CkanSource(HDX).fetch(ctx, "title:population", 5)
    assert ":" not in seen[0].url.params["q"]


async def test_ckan_relaxes_query_when_no_results():
    queries = []

    def handler(req):
        q = req.url.params["q"]
        queries.append(q)
        empty = {"success": True, "result": {"results": []}}
        return httpx.Response(200, json=fixture("ckan_hdx.json") if len(queries) == 3 else empty)

    ctx = Ctx(Http(httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    items = await CkanSource(HDX).search(ctx, parse("中国各省近十年人口 GDP 城镇化率"), 5)
    assert items
    assert [len(q.split()) for q in queries] == sorted(
        [len(q.split()) for q in queries], reverse=True
    )


def test_ckan_config_requires_url_pattern():
    with pytest.raises(ValueError):
        CkanSource(spec("x", "ckan", base_url="https://x.org/api"))


# ───────────── 国家统计局 ─────────────


def test_nbs_dataset_has_unique_url_per_indicator_and_api_resource():
    rows = fixture("nbs_search.json")["data"]["data"]
    items = [nbs_to_dataset(r) for r in rows]
    assert len({d.url for d in items}) == len(items)  # 否则去重会把它们全合并
    d = items[0]
    assert d.publisher == "国家统计局" and d.formats == ["API"] and d.license is None
    assert d.period and "最新" in d.description
    api = d.resources[0]
    assert api.kind == "api" and api.url.endswith("/stream/esData")
    body = json.loads(api.body)  # 请求体是独立字段，可直接复制
    assert body["cid"] and body["indicatorIds"] and body["rootId"]


def test_nbs_missing_fields_is_shape_error():
    with pytest.raises(ShapeError):
        nbs_to_dataset({"show_name": "x"})


async def test_nbs_searches_each_concept_and_keeps_latest_period_per_indicator():
    rows = fixture("nbs_search.json")["data"]["data"]
    dup = {**rows[0], "dt": "2023", "dt_name": "2023年"}  # 同指标同地区的旧一期
    seen = []

    def handler(req):
        seen.append(req.url.params["search"])
        return httpx.Response(
            200, json={"success": True, "data": {"data": [rows[0], dup, rows[1]]}}
        )

    ctx = Ctx(Http(httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    items = await NbsSource(spec("nbs", "nbs", langs=("zh",))).search(ctx, parse("人口 GDP"), 20)
    assert set(seen) == {"人口", "国内生产总值"}  # GDP 反查成中文词
    assert len(items) == 2  # 旧一期被合并
    assert items[0].period == "2024年"


def test_nbs_not_supported_for_unmappable_english_query():
    assert not NbsSource(spec("nbs", "nbs")).supports(parse("zebra giraffe"))
    assert NbsSource(spec("nbs", "nbs")).supports(parse("人口"))


# ───────────── Dataseek 桥接 ─────────────


def fake_dsk(tmp_path, body: str) -> str:
    """造一个假的 dsk：写成 .py，交给解释器执行（Windows 上没有执行位，直接 exec 会 WinError 193）。"""
    script = tmp_path / "dsk.py"
    script.write_text(body + "\n")
    return str(script)


def bridge(exe):
    b = DataseekSource(spec("dataseek", "dataseek"), exclude=["zenodo", "datacite"])
    b.exe = exe
    return b


async def test_dataseek_parses_real_output(tmp_path):
    payload = (FIX / "dataseek.json").read_text(encoding="utf-8")
    exe = fake_dsk(tmp_path, f"import sys\nsys.stdout.write({payload!r})")
    items = await bridge(exe).search(Ctx(Http()), parse("gdp population"), 5)
    assert items and items[0].source == "dataseek"
    assert items[0].sources  # 保留底层仓库名用于展示
    assert items[0].url.startswith("http")


async def test_dataseek_passes_excludes_and_uses_arg_array(tmp_path):
    log = tmp_path / "argv.json"
    exe = fake_dsk(
        tmp_path,
        "import sys, json\n"
        f"open({str(log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'results': []}))",
    )
    await bridge(exe).fetch(Ctx(Http()), "population; rm -rf /", 5)  # 不经 shell，分号只是普通字符
    argv = json.loads(log.read_text())
    assert argv[0] == "search" and argv[1] == "population; rm -rf /"
    assert argv[argv.index("-x") + 1] == "datacite,zenodo"


async def test_dataseek_non_json_output_is_shape_error(tmp_path):
    exe = fake_dsk(tmp_path, "print('not json')")
    with pytest.raises(ShapeError):
        await bridge(exe).fetch(Ctx(Http()), "x", 5)


async def test_dataseek_nonzero_exit_is_outage(tmp_path):
    exe = fake_dsk(tmp_path, "import sys\nsys.stderr.write('boom')\nsys.exit(1)")
    with pytest.raises(SourceError) as e:
        await bridge(exe).fetch(Ctx(Http()), "x", 5)
    assert e.value.is_outage and "boom" in str(e.value)


def test_dataseek_unsupported_when_not_installed():
    b = bridge(None)
    assert not b.supports(parse("population"))


# ───────────── 注册表 ─────────────


def test_registry_loads_and_builds_all_sources():
    specs = load_specs()
    ids = [s.id for s in specs]
    assert len(ids) == len(set(ids)) and {"nbs", "zenodo", "datacite", "dataseek"} <= set(ids)
    assert [s.id for s in build_sources()] == ids


def test_dataseek_excludes_exactly_the_native_sources_enabled():
    out = {s.id: s for s in build_sources(exclude={"hdx"})}
    assert "hdx" not in out["dataseek"].exclude  # hdx 被停用，就让 Dataseek 去查它
    assert {"zenodo", "datacite", "data-gov-uk"} <= set(out["dataseek"].exclude)


def test_only_and_exclude_and_unknown():
    assert [s.id for s in build_sources(only={"nbs"})] == ["nbs"]
    assert "nbs" not in [s.id for s in build_sources(exclude={"nbs"})]
    with pytest.raises(ValueError, match="未知来源"):
        build_sources(only={"nope"})


async def test_inspect_url_maps_files_and_normalizes_formats(tmp_path, monkeypatch):
    from datafinder.sources import dataseek as ds

    payload = {
        "schema": "dataseek-inspect/1",
        "files": [
            {"format": "text/csv", "url": "https://zenodo.org/api/records/1/files/a.csv/content"},
            {"format": "text/tab-separated-values", "url": "https://x.org/api/access/datafile/9"},
            {"format": "application/octet-stream", "url": "https://x.org/files/Prov0010.rar"},
            {"format": "application/octet-stream", "url": "https://x.org/files/blob"},
            {"format": "text/csv", "url": "ftp://x.org/not-http.csv"},
        ],
    }
    exe = fake_dsk(tmp_path, f"import sys, json\nsys.stdout.write({json.dumps(payload)!r})")
    monkeypatch.setattr(ds, "find_executable", lambda: exe)
    res = await ds.inspect_url("https://zenodo.org/records/1")
    assert [(r.name, r.format) for r in res] == [
        ("a.csv", "CSV"),  # Zenodo 的 .../files/名字/content 取"名字"
        ("9", "TSV"),
        ("Prov0010.rar", "RAR"),  # octet-stream 看扩展名
        ("blob", None),  # 无法判断就不编造
    ]


# ───────────── 中英双路检索 ─────────────

ZH_SPEC = spec("zenodo", "zenodo", options={"zh_query": True})


def _zenodo_payload(title, rid):
    p = fixture("zenodo.json")
    hit = p["hits"]["hits"][0]
    hit = {
        **hit,
        "id": rid,
        "metadata": {**hit["metadata"], "title": title},
        "links": {**hit["links"], "self_html": f"https://zenodo.org/records/{rid}"},
        "title": title,
    }
    return {"hits": {"hits": [hit]}}


async def test_zh_query_searches_both_languages_and_merges():
    seen = []

    def handler(req):
        q = req.url.params["q"]
        seen.append(q)
        zh = any("\u4e00" <= ch <= "\u9fff" for ch in q)
        return httpx.Response(
            200, json=_zenodo_payload("第七次人口普查" if zh else "Census tables", 2 if zh else 1)
        )

    ctx = Ctx(Http(httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    items = await ZenodoSource(ZH_SPEC).search(ctx, parse("人口普查"), 10)
    assert sorted(d.title for d in items) == ["Census tables", "第七次人口普查"]
    assert any(c for q in seen for c in q if "\u4e00" <= c <= "\u9fff")  # 确实发了中文查询
    assert any(q.isascii() for q in seen)  # 也发了英文查询


async def test_zh_query_is_off_by_default():
    seen = []

    def handler(req):
        seen.append(req.url.params["q"])
        return httpx.Response(200, json=_zenodo_payload("Census tables", 1))

    ctx = Ctx(Http(httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    await ZenodoSource(spec("zenodo", "zenodo")).search(ctx, parse("人口普查"), 10)
    assert all(q.isascii() for q in seen)


async def test_one_language_failing_still_returns_the_other():
    def handler(req):
        q = req.url.params["q"]
        if any("\u4e00" <= ch <= "\u9fff" for ch in q):
            return httpx.Response(500)
        return httpx.Response(200, json=_zenodo_payload("Census tables", 1))

    ctx = Ctx(Http(httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    items = await ZenodoSource(ZH_SPEC).search(ctx, parse("人口普查"), 10)
    assert [d.title for d in items] == ["Census tables"]


async def test_both_languages_failing_is_an_error():
    ctx = Ctx(Http(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))))
    with pytest.raises(SourceError):
        await ZenodoSource(ZH_SPEC).search(ctx, parse("人口普查"), 10)


def test_cache_key_distinguishes_zh_query_setting():
    plan = parse("人口普查")
    assert ZenodoSource(ZH_SPEC).cache_query(plan) != ZenodoSource(
        spec("zenodo", "zenodo")
    ).cache_query(plan)
