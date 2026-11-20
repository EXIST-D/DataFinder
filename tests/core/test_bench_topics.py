from pathlib import Path

from typer.testing import CliRunner

from datafinder.bench import bench_app
from datafinder.bench.topics import TopicCase, evaluate, load_topics, recall

BENCH = Path(__file__).parents[2] / "bench"


def plan(indicators=(), portals=(), yearbooks=(), ind_hint=None, yb_hint=None):
    return {
        "sources": {
            "direct": {"items": [{"indicator": i, "path": "库 › 类"} for i in indicators], "hint": ind_hint},
            "yearbooks": {"items": [{"title": t, "chapter": "章"} for t in yearbooks], "hint": yb_hint},
            "regional_portals": {"items": [{"name": p} for p in portals[:1]]},
            "official_portals": {"items": [{"name": p} for p in portals[1:]]},
        }
    }


CASE = TopicCase("t", "主题", None, {"portals": [["统计局"]], "indicators": [["工业增加值"], ["R&D", "研究与试验发展"]], "yearbooks": [["规模以上"]]})


def test_evaluate_counts_hits_and_lists_misses_with_synonym_groups():
    r = evaluate(CASE, plan(indicators=["工业增加值 (亿元)", "研究与试验发展经费"], portals=["河北省统计局"], yearbooks=["规模以上工业企业"]))
    assert {k: (v["hit"], v["total"]) for k, v in r["kinds"].items()} == {"portals": (1, 1), "indicators": (2, 2), "yearbooks": (1, 1)}
    r = evaluate(CASE, plan(indicators=["工业增加值"], portals=["别的"], yearbooks=[]))
    assert r["kinds"]["indicators"]["misses"] == ["R&D/研究与试验发展"]
    assert r["kinds"]["portals"]["misses"] == ["统计局"] and r["kinds"]["yearbooks"]["hit"] == 0


def test_missing_indexes_are_skipped_not_failed():
    r = evaluate(CASE, plan(ind_hint="没建索引", yb_hint="没建目录", portals=["统计局"]))
    assert r["kinds"]["indicators"]["skipped"] and r["kinds"]["yearbooks"]["skipped"]
    assert recall([r]) == (1, 1)  # 只统计能评估的类别


def test_topic_files_are_well_formed():
    for name in ("topics.yaml", "topics.holdout.yaml"):
        cases = load_topics(BENCH, name)
        assert len(cases) >= 8
        assert len({c.id for c in cases}) == len(cases)
        for c in cases:
            assert c.topic and any(c.expect.values()), c.id
    dev = {c.id for c in load_topics(BENCH)}
    hold = {c.id for c in load_topics(BENCH, "topics.holdout.yaml")}
    assert not dev & hold, "留出集不能和开发集共用选题"


def test_topics_command_fails_clearly_without_portal_db():
    result = CliRunner().invoke(bench_app, ["topics", "--dir", str(BENCH)])
    assert result.exit_code == 1 and "datafinder sync" in result.output
