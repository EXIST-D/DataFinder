import pytest

from datafinder.catalog import taxonomy
from datafinder.catalog.rules import RegionTag, RuleSet, Target, normalize_label

RAW = {
    "topic_rules": [
        {"scope": "top", "topics": ["t.industry"], "any": ["工业"]},
        {"scope": "top", "topics": ["t.edu"], "any": ["教育"]},
        {"scope": "top", "topics": ["t.sci"], "any": ["科技"]},
        {"scope": "title", "topics": ["t.energy"], "any": ["能源消费"]},
        {
            "scope": "title",
            "under": "t.industry",
            "topics": ["t.industry.perf"],
            "any": ["经济指标"],
            "none": ["价格"],
        },
    ],
    "region_rules": [
        {"scope": "top", "any": ["香港"], "region": "CN-HK", "scope_mode": "exact"},
        {
            "scope": "title",
            "any": ["各市"],
            "region": None,
            "relation": "reports_at",
            "granularity": "prefecture",
        },
    ],
}
RULES = RuleSet.from_raw(RAW)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("十三、工 业", "工业"),
        ("第十四篇 工　业", "工业"),
        ("（二）教育", "教育"),
        ("附录1 国际统计资料", "附录1国际统计资料"),
        ("人口", "人口"),
    ],
)
def test_normalize_label_strips_numbering_and_spaces(raw, expected):
    assert normalize_label(raw) == expected


def test_composite_chapter_gets_multiple_topics():
    assert RULES.topics(Target(top="十九、教育和科技")) == ["t.edu", "t.sci"]


def test_title_rules_add_extra_topics_without_replacing_top_level_ones():
    t = Target(top="工业", title="能源消费量")
    assert RULES.topics(t) == ["t.industry", "t.energy"]


def test_under_rule_only_fires_when_parent_topic_matched_and_none_excludes():
    assert RULES.topics(Target(top="工业", title="企业主要经济指标")) == [
        "t.industry",
        "t.industry.perf",
    ]
    assert "t.industry.perf" not in RULES.topics(Target(top="教育", title="企业主要经济指标"))
    assert "t.industry.perf" not in RULES.topics(Target(top="工业", title="价格经济指标"))


def test_unmatched_returns_empty_not_a_guess():
    assert RULES.topics(Target(top="其他社会活动", title="某表")) == []


def test_region_rules_distinguish_specific_region_from_reporting_granularity():
    assert RULES.regions(Target(top="香港特别行政区主要社会经济指标")) == [
        RegionTag("CN-HK", "covers", None, "exact")
    ]
    (tag,) = RULES.regions(Target(title="各市工业增加值"))
    assert tag.region is None and tag.relation == "reports_at" and tag.granularity == "prefecture"
    assert tag.evidence_status == "inferred"  # 标题推断永远不是 verified
    assert RULES.regions(Target(top="工业", title="工业增加值")) == []


def test_register_taxonomy_validates_parents_and_unique_ids():
    good = {
        "topics": [
            {"id": "x.a", "name_zh": "甲"},
            {"id": "x.a.b", "parent_id": "x.a", "name_zh": "乙"},
        ]
    }
    taxonomy.register_taxonomy("tmp_ok", good)
    try:
        assert taxonomy.get("x.a.b").level == 2 and taxonomy.ancestors("x.a.b") == ["x.a"]
        assert taxonomy.descendants("x.a") == ["x.a.b"]
        assert taxonomy.resolve("乙") == "x.a.b" and taxonomy.resolve("nope") is None
        with pytest.raises(ValueError, match="父主题"):
            taxonomy.register_taxonomy(
                "tmp_bad", {"topics": [{"id": "y.b", "parent_id": "y.a", "name_zh": "乙"}]}
            )
        with pytest.raises(ValueError, match="重复"):
            taxonomy.register_taxonomy("tmp_dup", {"topics": [{"id": "x.a", "name_zh": "甲"}]})
    finally:
        taxonomy._TOPICS.pop("tmp_ok", None)
        taxonomy._RULES.pop("tmp_ok", None)
        taxonomy._VERSIONS.pop("tmp_ok", None)
