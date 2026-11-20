import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from datafinder.engine.cache import Cache
from datafinder.engine.errors import SourceError
from datafinder.engine.http import Http
from datafinder.sources.nbs_tree import (
    DATABASES,
    NbsClient,
    dts_window,
    parse_series,
    valid_area,
    valid_id,
)

FIX = json.loads((Path(__file__).parent / "fixtures" / "nbs_tree.json").read_text(encoding="utf-8"))
TODAY = date(2026, 11, 20)
WZWS = "<html><body><h1>Please enable JavaScript and refresh the page.</h1><script>WZWS</script></body></html>"


def ok(data):
    return httpx.Response(
        200, json={"data": data, "success": True, "state": 20000, "message": "成功"}
    )


def make(handler, tmp_path):
    calls = []

    def wrapped(req):
        calls.append(req)
        return handler(req)

    http = Http(httpx.AsyncClient(transport=httpx.MockTransport(wrapped)))
    return NbsClient(http, Cache(tmp_path / "c.db")), calls


# ───────────── 时间窗口（三种类型都已对真实接口验证过）─────────────


def test_dts_window_per_database_type():
    assert dts_window("YY", 5, TODAY) == "2017YY-2026YY"
    assert dts_window("MM", 5, TODAY) == "202602MM-202611MM"
    assert dts_window("SS", 5, TODAY) == "202403SS-202604SS"


def test_dts_window_never_uses_unsupported_last_tokens():
    # 实测 "LAST5" 返回 0 期，所以只能给明确区间
    for t in ("YY", "MM", "SS"):
        assert "LAST" not in dts_window(t, 5, TODAY)


def test_dts_window_crosses_year_boundaries_correctly():
    assert dts_window("MM", 1, date(2026, 1, 15)) == "202512MM-202601MM"
    assert dts_window("SS", 1, date(2026, 2, 1)) == "202504SS-202601SS"


def test_database_table_matches_official_codes():
    assert set(DATABASES) == {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "12", "13", "14"}
    assert DATABASES["6"] == ("分省年度数据", "YY") and DATABASES["5"][1] == "SS"


def test_id_validation():
    assert valid_id("16f09026c6b34e688b307c4b7c9f4633") and not valid_id("../../etc/passwd")
    assert valid_id("") is False
    assert valid_area("110000000000") and not valid_area("11000") and not valid_area("北京")


# ───────────── 分类树 ─────────────


async def test_tree_normalizes_nodes_and_caches_for_24h(tmp_path):
    handler = lambda req: ok(FIX["children"])  # noqa: E731
    c, calls = make(handler, tmp_path)
    nodes = await c.tree("6", FIX["root6"][0]["_id"])
    assert nodes[0]["name"] == "综合" and nodes[0]["is_leaf"] is False
    assert nodes[0]["root_id"] and len(nodes[0]["root_id"]) == 32
    again = await c.tree("6", FIX["root6"][0]["_id"])
    assert again == nodes and len(calls) == 1  # 第二次走缓存，不再打国家统计局


async def test_tree_sends_required_headers_and_params(tmp_path):
    c, calls = make(lambda req: ok(FIX["root6"]), tmp_path)
    await c.tree("6", "")
    req = calls[0]
    assert req.headers["referer"].endswith("/page.html") and req.headers["client"] == "pc"
    assert req.url.params["code"] == "6" and req.url.params["pid"] == ""


async def test_unknown_database_is_rejected_before_any_request(tmp_path):
    c, calls = make(lambda req: ok([]), tmp_path)
    with pytest.raises(ValueError):
        await c.tree("99", "")
    assert not calls


# ───────────── 指标 / 地区 ─────────────


async def test_indicators_keep_unit_and_statistical_note(tmp_path):
    c, _ = make(lambda req: ok(FIX["indicators"]), tmp_path)
    inds = await c.indicators(FIX["leaf"]["_id"])
    assert inds[0]["name"] and inds[0]["unit"] == "个"


async def test_regions_returns_catalogs_and_first_catalog_regions(tmp_path):
    def handler(req):
        if req.url.path.endswith("getDaCatalogTreeByIndicatorCid"):
            return ok(FIX["da_catalogs"])
        assert req.url.params["daCid"] == FIX["da_catalogs"][0]["_id"]
        return ok(FIX["das"]["data"])

    c, calls = make(handler, tmp_path)
    r = await c.regions(FIX["leaf"]["_id"])
    assert r["catalogs"][0]["name"] == "全部地区" and r["regions"][0] == {
        "text": "北京市",
        "value": "110000000000",
    }
    await c.regions(FIX["leaf"]["_id"])
    assert len(calls) == 2  # 第二次整体走缓存


async def test_regions_without_catalogs_means_national_only(tmp_path):
    c, _ = make(lambda req: ok([]), tmp_path)
    r = await c.regions(FIX["leaf"]["_id"])
    assert r["regions"] == [{"text": "全国", "value": "000000000000"}]


# ───────────── 取值 ─────────────


async def test_series_builds_multi_region_request(tmp_path):
    def handler(req):
        body = json.loads(req.content)
        assert body["showType"] == "3" and body["rootId"] == "R" * 32
        assert body["dts"] == ["2017YY-2026YY"] and len(body["das"]) == 2
        assert body["indicatorIds"] == ["I" * 32]
        return ok(FIX["series_multi"])

    c, _ = make(handler, tmp_path)
    res = await c.series(
        code="6",
        cid="C" * 32,
        indicator_id="I" * 32,
        root_id="R" * 32,
        areas=[
            {"text": "北京市", "value": "110000000000"},
            {"text": "上海市", "value": "310000000000"},
        ],
        n=5,
        today=TODAY,
    )
    assert res["unit"] == "万人" and "北京" in res["areas"] and len(res["areas"]) == 3
    assert res["periods"][0]["name"] == "2023年" and len(res["periods"]) == 3


def test_parse_series_filters_unpublished_and_sorts_latest_first():
    data = [
        {
            "code": "202602SS",
            "name": "2026年第二季度",
            "values": [{"area": "全国", "value": "1", "du_name": "亿元"}],
        },
        {
            "code": "202604SS",
            "name": "2026年第四季度",
            "values": [{"area": "全国", "value": "", "du_name": "亿元"}],
        },
        {
            "code": "202603SS",
            "name": "2026年第三季度",
            "values": [{"area": "全国", "value": "3", "du_name": "亿元"}],
        },
    ]
    res = parse_series(data, 8)
    assert [p["code"] for p in res["periods"]] == ["202603SS", "202602SS"]  # 空值期被过滤，新的在前
    assert parse_series(data, 1)["periods"][0]["code"] == "202603SS"  # 取最近 n 期


def test_parse_series_accepts_both_response_shapes():
    old = [
        {
            "code": "2024YY",
            "name": "2024年",
            "values": [{"da_name": "全国", "value": "9", "du_name": "人"}],
        }
    ]
    new = [
        {
            "code": "2024YY",
            "name": "2024年",
            "values": [{"area": "北京", "value": "9", "du_name": "人"}],
        }
    ]
    assert parse_series(old, 3)["areas"] == ["全国"] and parse_series(new, 3)["areas"] == ["北京"]


# ───────────── 防火墙：暂停访问，不重试不绕过 ─────────────


async def test_waf_challenge_pauses_further_requests(tmp_path):
    c, calls = make(
        lambda req: httpx.Response(200, text=WZWS, headers={"content-type": "text/html"}), tmp_path
    )
    with pytest.raises(SourceError) as first:
        await c.tree("6", "")
    assert first.value.kind == "rate_limited"
    n = len(calls)
    with pytest.raises(SourceError) as second:
        await c.tree("3", "")
    assert "暂时休息" in str(second.value) and len(calls) == n  # 第二次根本没有发请求
