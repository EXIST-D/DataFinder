import json
from pathlib import Path

import httpx
import pytest

from datafinder.engine.cache import Cache
from datafinder.engine.http import Http
from datafinder.service import ServiceError, _resolve_indicators, resolve_regions
from datafinder.sources.nbs_tree import NbsClient, explicit_window, parse_tidy

FIX = json.loads((Path(__file__).parent / "fixtures" / "nbs_tree.json").read_text(encoding="utf-8"))
REGIONS = [
    {"text": "北京市", "value": "110000000000"},
    {"text": "内蒙古自治区", "value": "150000000000"},
    {"text": "广西壮族自治区", "value": "450000000000"},
    {"text": "河北省", "value": "130000000000"},
    {"text": "全国", "value": "000000000000"},
]


def ok(data):
    return httpx.Response(200, json={"data": data, "success": True, "state": 20000})


def make(handler, tmp_path):
    http = Http(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return NbsClient(http, Cache(tmp_path / "c.db"))


# ───────────── 区间与解析 ─────────────


def test_explicit_window_formats_per_type():
    assert explicit_window("YY", "2015", "2024") == "2015YY-2024YY"
    assert explicit_window("MM", "201501", "202412") == "201501MM-202412MM"
    assert explicit_window("SS", "202401", "202504") == "202401SS-202504SS"


@pytest.mark.parametrize(
    ("dt", "start", "end"),
    [("YY", "15", "2024"), ("MM", "2015", "202412"), ("SS", "202405", "202504"), ("YY", "2024", "2020")],
)
def test_explicit_window_rejects_bad_input_with_hint(dt, start, end):
    with pytest.raises(ValueError):
        explicit_window(dt, start, end)


def test_parse_tidy_multi_region_rows_and_recent_n():
    rows = parse_tidy(FIX["series_multi"], 2)
    assert {r["地区"] for r in rows} == {"北京", "上海市", "广东省"}
    per = {}
    for r in rows:
        per.setdefault(r["地区"], []).append(r["期别代码"])
    assert all(v == ["2023YY", "2022YY"] for v in per.values())  # 每个地区最近 2 期
    assert rows[0]["单位"] == "万人" and rows[0]["地区代码"] == "110000000000"
    # 指标名取官网显示名（去掉重复的单位），而不是 `_name`（标准名/父级名）
    assert rows[0]["指标"] == "年末常住人口"


def test_parse_tidy_drops_unpublished_and_without_n_keeps_all():
    data = [{"code": "2025YY", "name": "2025年", "values": [{"_name": "x", "value": "", "area": "北京", "areaCode": "1"}]},
            {"code": "2024YY", "name": "2024年", "values": [{"_name": "x", "value": "5", "area": "北京", "areaCode": "1"}]}]
    assert [r["期别代码"] for r in parse_tidy(data, None)] == ["2024YY"]


def test_parse_tidy_rejects_wrong_shape():
    with pytest.raises(Exception, match="不是列表"):
        parse_tidy({"oops": 1}, 3)


# ───────────── 取表请求 ─────────────


async def test_table_sends_one_request_per_indicator_and_keeps_all_rows(tmp_path):
    # 实测教训：多指标一次请求时接口会悄悄只回其中一个，所以必须一个指标一个请求
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        assert body["showType"] == "3" and body["dts"] == ["2020YY-2023YY"] and len(body["das"]) == 2
        tag = body["indicatorIds"][0]
        data = [{"code": "2023YY", "name": "2023年", "values": [
            {"_id": tag, "i_showname": f"指标{tag[0]} (亿元) ", "_name": "父级", "du_name": "亿元", "value": "1", "area": "北京\t", "areaCode": "110000000000"}]}]
        return ok(data)

    c = make(handler, tmp_path)
    rows = await c.table(code="6", cid="C" * 32, indicator_ids=["a" * 32, "b" * 32], root_id="R" * 32,
                         areas=REGIONS[:2], start="2020", end="2023")
    assert [b["indicatorIds"] for b in bodies] == [["a" * 32], ["b" * 32]]
    assert {r["指标"] for r in rows} == {"指标a", "指标b"}  # 两个指标都在
    assert {r["地区"] for r in rows} == {"北京"}  # 地区名里的制表符/空白被清掉


def testclean_name_strips_only_matching_unit():
    from datafinder.sources.nbs_tree import clean_name

    assert clean_name("工业增加值 (亿元) ", "亿元") == "工业增加值"
    assert clean_name("城镇化率 (%)", "%") == "城镇化率"
    assert clean_name("人口（抽样调查）", "万人") == "人口（抽样调查）"  # 括号不是单位就保留
    assert clean_name(None, None) == ""


async def test_table_limits_indicator_count(tmp_path):
    c = make(lambda r: ok([]), tmp_path)
    with pytest.raises(ValueError):
        await c.table(code="6", cid="C" * 32, indicator_ids=[f"{i:032x}" for i in range(11)],
                      root_id="R" * 32, areas=REGIONS[:1])


async def test_search_national_maps_database_code(tmp_path):
    rows = [{"show_name": "国内生产总值 (亿元)", "cid": "c" * 32, "indic_id": "i" * 32, "da_name": "全国",
             "dt_name": "2025年", "value": "1349084.1", "dt_type": "YY", "type_text": "指标",
             "treeinfo_globalid": "r" * 32 + ".x.y"}, {"oops": 1}]
    c = make(lambda r: ok({"data": rows}), tmp_path)
    (hit,) = await c.search_national("国内生产总值")
    assert hit["db"] == "3" and hit["root_id"] == "r" * 32 and hit["indicator_id"] == "i" * 32


# ───────────── 名称解析（助手常用中文简称）─────────────


@pytest.mark.parametrize(("name", "code"), [("河北", "130000000000"), ("河北省", "130000000000"), ("内蒙古", "150000000000"),
                                          ("广西", "450000000000"), ("北京", "110000000000"), ("110000000000", "110000000000")])
def test_resolve_regions_accepts_short_names_and_codes(name, code):
    (r,) = resolve_regions(REGIONS, [name])
    assert r["value"] == code


def test_resolve_regions_all_excludes_national_and_unknown_is_helpful():
    got = resolve_regions(REGIONS, ["全部"])
    assert len(got) == 4 and all(r["value"] != "000000000000" for r in got)
    with pytest.raises(ServiceError, match="没有地区「火星」"):
        resolve_regions(REGIONS, ["火星"])
    with pytest.raises(ServiceError, match="不支持地区代码"):
        resolve_regions(REGIONS, ["999999999999"])
    with pytest.raises(ServiceError, match="不能为空"):
        resolve_regions(REGIONS, [])


def test_resolve_indicators_by_id_name_and_ambiguity():
    cat = [{"id": "a" * 32, "name": "工业增加值"}, {"id": "b" * 32, "name": "工业总产值"}, {"id": "c" * 32, "name": "工业总产值指数"}]
    assert [i["id"] for i in _resolve_indicators(cat, ["工业增加值", "b" * 32])] == ["a" * 32, "b" * 32]
    assert _resolve_indicators(cat, ["工业总产值"])[0]["id"] == "b" * 32  # 精确优先于模糊
    with pytest.raises(ServiceError, match="匹配多个"):
        _resolve_indicators(cat, ["工业总"])
    with pytest.raises(ServiceError, match="没有名为"):
        _resolve_indicators(cat, ["不存在"])
