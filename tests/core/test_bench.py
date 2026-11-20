import csv
from pathlib import Path

from datafinder.bench import (
    grade_of,
    keys_of,
    load_judgments,
    load_queries,
    ndcg_at_k,
    precision_at,
)
from datafinder.models import Dataset

BENCH = Path(__file__).parents[2] / "bench"


def test_ndcg_is_one_for_ideal_order_and_zero_without_relevant_items():
    assert ndcg_at_k([2, 1, 0], [2, 1, 0, 0]) == 1.0
    assert ndcg_at_k([0, 0, 0], [2, 1]) == 0.0
    assert ndcg_at_k([0, 0], []) == 0.0


def test_ndcg_rewards_putting_better_items_first():
    pool = [2, 1, 0]
    assert ndcg_at_k([2, 1, 0], pool) > ndcg_at_k([1, 2, 0], pool) > ndcg_at_k([0, 1, 2], pool)


def test_precision_at_counts_grade_one_or_more():
    assert precision_at([2, 1, 0, 0, 1], 5) == 0.6
    assert precision_at([], 5) == 0.0


def test_load_judgments_skips_comments(tmp_path):
    (tmp_path / "judgments.tsv").write_text(
        "# 注释\nq01\tdoi:10.1/x\t2\tagent\tT\nq01\turl:a.org/1\t0\tagent\tU\n", encoding="utf-8"
    )
    assert load_judgments(tmp_path) == {"q01": {"doi:10.1/x": 2, "url:a.org/1": 0}}


def test_grade_lookup_matches_any_identity_key():
    d = Dataset("Some dataset title here", "https://a.org/1", "s", doi="10.5281/zenodo.1")
    assert grade_of(d, {"doi:10.5281/zenodo.1": 2}) == 2  # 按 DOI 命中
    assert grade_of(d, {"url:a.org/1": 1}) == 1  # 按 URL 命中
    assert grade_of(d, {"url:other.org": 2}) is None  # 未标注
    assert keys_of(d)[0].startswith("doi:")


def test_shipped_benchmark_files_are_consistent():
    queries = load_queries(BENCH)
    ids = [q.id for q in queries]
    assert len(ids) == len(set(ids)) >= 10
    judged = load_judgments(BENCH)
    assert set(judged) <= set(ids)
    with (BENCH / "judgments.tsv").open(encoding="utf-8") as fh:
        rows = [r for r in csv.reader(fh, delimiter="\t") if r and not r[0].startswith("#")]
    assert rows
    for r in rows:
        assert r[2] in {"0", "1", "2"}, r
        assert r[3] in {"agent", "human"}, "标注者必须写明，不能让机器标注冒充人工"
    # 每个问句至少有一条被标为相关，否则 nDCG 没有意义
    for qid in ids:
        assert any(g > 0 for g in judged[qid].values()), qid
