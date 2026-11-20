from pathlib import Path

from typer.testing import CliRunner

from datafinder.bench import bench_app
from datafinder.bench.facets import FacetCase, evaluate, load_cases, matches

BENCH = Path(__file__).parents[2] / "bench"

ITEM = {
    "title": "规模以上工业增加值",
    "kind": "table",
    "source_id": "yearbook.CN-SD",
    "path": "第十四篇 工业",
    "delivery": "download",
    "usability": "image",
    "topic_ids": ["cnstat.industry"],
    "frequency": None,
    "versions": [2025, 2024],
    "regions": [{"region_id": "CN-SD"}],
    "period": {"start": None},
    "missing_requirements": ["观测期未知（仅有出版年 2025）"],
}


def resp(*items, hints=()):
    return {"results": list(items), "hints": list(hints)}


def test_matches_supports_every_condition_key_with_or_values():
    assert matches(
        ITEM, {"title_any": ["工业", "xx"], "kind": ["table", "indicator"], "delivery": "download"}
    )
    assert matches(
        ITEM,
        {
            "source_prefix": "yearbook.",
            "path_prefix": "第十四篇",
            "topic": "cnstat.industry",
            "region": "CN-SD",
        },
    )
    assert matches(
        ITEM,
        {
            "period_known": False,
            "missing_any": "观测期未知",
            "versions_min": 2,
            "usability": "image",
        },
    )
    assert not matches(ITEM, {"delivery": "generate"}) and not matches(ITEM, {"period_known": True})
    assert not matches(ITEM, {"title_any": ["人口"]}) and not matches(ITEM, {"versions_min": 3})


def test_evaluate_reports_every_kind_of_failure_and_passes_clean_results():
    case = FacetCase(
        "c",
        "q",
        {},
        {
            "hit": [{"delivery": "download"}],
            "all": [{"kind": "table"}],
            "forbid": [{"source_prefix": "nbs"}],
        },
    )
    assert evaluate(case, resp(ITEM)) == []
    bad = FacetCase(
        "c",
        "q",
        {},
        {
            "hit": [{"delivery": "generate"}],
            "all": [{"kind": "indicator"}],
            "forbid": [{"usability": "image"}],
            "min_results": 2,
        },
    )
    fails = evaluate(bad, resp(ITEM))
    assert (
        len(fails) == 4
        and any("结果太少" in f for f in fails)
        and any("不该出现" in f for f in fails)
    )


def test_max_results_hint_contains_and_default_minimum():
    assert (
        evaluate(
            FacetCase("c", "", {}, {"max_results": 0, "hint_contains": "无区分度"}),
            resp(hints=["关键词只有无区分度的词"]),
        )
        == []
    )
    assert any(
        "结果太多" in f for f in evaluate(FacetCase("c", "", {}, {"max_results": 0}), resp(ITEM))
    )
    assert evaluate(FacetCase("c", "", {}, {}), resp()) == [
        "结果太少：0 < 1"
    ]  # 没写上限时默认至少 1 条
    assert any(
        "提示里应包含" in f
        for f in evaluate(FacetCase("c", "", {}, {"max_results": 0, "hint_contains": "x"}), resp())
    )


def test_case_files_are_well_formed_and_dev_and_holdout_do_not_share_ids():
    dev, hold = load_cases(BENCH), load_cases(BENCH, "facets.holdout.yaml")
    assert len(dev) >= 20 and len(hold) >= 8
    assert not {c.id for c in dev} & {c.id for c in hold}
    for c in [*dev, *hold]:
        assert c.expect and (c.query or c.filters), c.id
        assert set(c.filters) <= {
            "topic",
            "region",
            "kind",
            "delivery",
            "granularity",
            "from",
            "to",
            "frequency",
            "mode",
            "region_match",
            "limit",
        }
    assert any("forbid" in c.expect for c in dev)  # 必须有"误导项"用例


def test_command_fails_clearly_without_catalog():
    result = CliRunner().invoke(bench_app, ["facets", "--dir", str(BENCH)])
    assert result.exit_code == 1 and "index" in result.output
