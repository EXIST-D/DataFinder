from pathlib import Path

from typer.testing import CliRunner

from datafinder.bench import bench_app
from datafinder.bench.topics import TopicCase, evaluate, load_topics, recall

BENCH = Path(__file__).parents[2] / "bench"


def plan(indicators=(), portals=(), tables=()):
    return {
        "sources": {
            "indicators": {"items": [{"title": i, "path": "库 › 类"} for i in indicators]},
            "tables": {"items": [{"title": t, "path": "章"} for t in tables]},
            "regional_portals": {"items": [{"name": p} for p in portals[:1]]},
            "official_portals": {"items": [{"name": p} for p in portals[1:]]},
        }
    }


CASE = TopicCase(
    "t",
    "主题",
    None,
    {
        "portals": [["统计局"]],
        "indicators": [["工业增加值"], ["R&D", "研究与试验发展"]],
        "tables": [["规模以上"]],
    },
)


def test_evaluate_counts_hits_and_lists_misses_with_synonym_groups():
    r = evaluate(
        CASE,
        plan(
            indicators=["工业增加值", "研究与试验发展经费"],
            portals=["河北省统计局"],
            tables=["规模以上工业企业"],
        ),
    )
    assert {k: (v["hit"], v["total"]) for k, v in r["kinds"].items()} == {
        "portals": (1, 1),
        "indicators": (2, 2),
        "tables": (1, 1),
    }
    r = evaluate(CASE, plan(indicators=["工业增加值"], portals=["别的"], tables=[]))
    assert r["kinds"]["indicators"]["misses"] == ["R&D/研究与试验发展"]
    assert r["kinds"]["portals"]["misses"] == ["统计局"] and r["kinds"]["tables"]["hit"] == 0
    assert recall([r]) == (1, 4)


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


def test_topics_command_fails_clearly_without_catalog():
    result = CliRunner().invoke(bench_app, ["topics", "--dir", str(BENCH)])
    assert (
        result.exit_code == 1 and "index" in result.output
    )  # 目录是空的：提示怎么建立，不悄悄给 0 分
