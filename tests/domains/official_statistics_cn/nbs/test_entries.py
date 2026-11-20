import pytest

from datafinder.domains.official_statistics_cn.nbs.entries import (
    FREQUENCY,
    category_entry,
    database_entry,
    dataset_entry,
    indicator_entry,
    period_from_title,
)

CID, ROOT = "c" * 32, "a" * 32


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("按行业分规模以上工业企业主要经济指标 (2012-2022)", (2012, 2022)),
        ("按行业分规模以上工业企业主要经济指标（2023-至今）", (2023, None)),
        ("按行业分工业企业主要经济指标 (2003-2011)", (2003, 2011)),
        ("按登记注册类型分规模以上工业企业经济指标 (-2022)", (None, None)),  # 起点不明：不猜
        ("地区生产总值", (None, None)),
    ],
)
def test_period_from_title_only_trusts_explicit_ranges(title, expected):
    assert period_from_title(title) == expected


def test_database_entries_carry_region_structure_by_database_type():
    assert [
        (r.region_id, r.granularity, r.scope_mode, r.evidence_status)
        for r in database_entry("3").regions
    ] == [("CN", "country", "exact", "structure")]
    assert (
        database_entry("6").regions[0].granularity == "province"
        and database_entry("6").regions[0].scope_mode == "descendants"
    )
    assert database_entry("8").regions[0].granularity == "prefecture"
    assert {r.region_id for r in database_entry("10").regions} == {"CN-HK", "CN-MO", "CN-TW"}
    assert database_entry("14").regions[0].region_id == "WORLD"
    assert (
        database_entry("6").frequency == FREQUENCY["YY"] == "annual"
        and database_entry("4").frequency == "monthly"
    )


def test_dataset_entry_hierarchy_topics_period_and_delivery():
    names = ["工业", "规模以上工业企业主要经济指标 (2012-2022)"]
    ds = dataset_entry("3", names, CID, ROOT)
    assert ds.resource_id == f"nbs:dataset:3/{CID}" and ds.parent_id == "nbs:category:3/工业"
    assert ds.path == "年度数据 › 工业 › 规模以上工业企业主要经济指标 (2012-2022)"
    assert (ds.period_start, ds.period_end, ds.frequency) == (2012, 2022, "annual")
    assert ds.extra["period_from"] == "title" and ds.verification_level == "source_asserted"
    assert {"cnstat.industry", "cnstat.industry.enterprise_performance"} <= {
        t.topic_id for t in ds.topics
    }
    assert ds.delivery == "generate" and ds.distributions[0].usability == "tabular"
    assert ds.regions[0].region_id == "CN" and ds.regions[0].granularity == "country"


def test_dataset_directly_under_database_has_database_as_parent():
    ds = dataset_entry("10", ["经济"], CID, ROOT)
    assert ds.parent_id == "nbs:database:10" and {t.topic_id for t in ds.topics} == {"cnstat.macro"}


def test_nested_categories_chain_parents():
    c = category_entry("6", ["国民经济核算", "分行业"])
    assert c.kind == "category" and c.parent_id == "nbs:category:6/国民经济核算"
    assert category_entry("6", ["国民经济核算"]).parent_id == "nbs:database:6"


def test_indicator_inherits_dataset_and_cleans_name_and_adds_title_topics():
    ds = dataset_entry("6", ["国民经济核算", "地区生产总值"], CID, ROOT)
    ind = indicator_entry(
        ds, {"id": "i" * 32, "name": "工业增加值 (亿元) ", "unit": "亿元", "mark": "指工业企业……"}
    )
    assert ind.title == "工业增加值" and ind.unit == "亿元"  # 单位另有字段，名称里不再重复
    assert (
        ind.resource_id == f"nbs:indicator:6/{CID}/{'i' * 32}" and ind.parent_id == ds.resource_id
    )
    assert ind.extra["indicator_id"] == "i" * 32 and ind.extra["mark"].startswith("指工业")
    assert {"cnstat.macro", "cnstat.industry", "cnstat.industry.value_added"} <= {
        t.topic_id for t in ind.topics
    }
    assert (
        ind.regions[0].granularity == "province" and ind.regions[0].evidence_status == "structure"
    )
    assert ind.distributions == ds.distributions and ind.frequency == "annual"


def test_title_region_hints_are_inferred_never_structure():
    ds = dataset_entry("6", ["城市概况", "各市行政区划"], CID, ROOT)
    hints = [r for r in ds.regions if r.evidence_status == "inferred"]
    assert any(r.granularity == "prefecture" for r in hints)
    assert all(r.evidence_status == "structure" for r in ds.regions if r not in hints)
