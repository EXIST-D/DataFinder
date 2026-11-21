"""地级/县级区域：按唯一名称识别、上级资源只在"报告粒度够细"时才覆盖下级。

区划数据来自领域包登记的官方版本（加载任一领域能力时自动登记），这里只验证核心的识别与覆盖语义。
"""

from datafinder.catalog import SearchRequest, regions
from datafinder.catalog import search as run_search

from .conftest import build  # noqa: F401  (保证合成目录夹具可用)


def test_names_resolve_only_when_unique_and_codes_always():
    assert regions.resolve("济南") == "CN-370100" and regions.resolve("济南市") == "CN-370100"
    assert regions.resolve("370102") == "CN-370102" and regions.resolve("历下区") == "CN-370102"
    assert regions.resolve("市中区") is None  # 重名：不猜
    assert regions.resolve("CN-210103") == "CN-210103"  # 但 id/代码可以
    assert regions.ancestors("CN-370102") == ["CN-370100", "CN-SD", "CN", "WORLD"]
    assert regions.resolve("山东") == "CN-SD"  # 省级不受影响


def test_province_level_data_does_not_cover_a_prefecture(cat):
    req = SearchRequest(query="增加值", kinds=["indicator"], region_ids=["济南"])
    assert run_search(cat, req).total == 0  # 全国分省库按省报告，没有济南市的数据
    ok = run_search(cat, SearchRequest(query="增加值", kinds=["indicator"], region_ids=["河北"]))
    assert ok.total > 0  # 省级查询照旧


def test_tables_reporting_at_prefecture_level_cover_its_cities(cat):
    req = SearchRequest(query="工业增加值", kinds=["table"], region_ids=["济南"])
    titles = {r["title"] for r in run_search(cat, req).to_dict()["results"]}
    assert any("工业增加值" in t for t in titles)  # 山东年鉴"按地市报告"的表
    other = SearchRequest(query="工业增加值", kinds=["table"], region_ids=["沈阳"])
    assert run_search(cat, other).total == 0  # 辽宁没有这类表：不会借山东的


def test_special_regions_win_over_same_named_country_entries():
    assert regions.resolve("香港") == "CN-HK"
    assert regions.resolve("澳门") == "CN-MO" and regions.resolve("台湾") == "CN-TW"
    assert regions.resolve("HK") == "HK"  # 国家代码本身仍然认
