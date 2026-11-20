import pytest

from datafinder.catalog import Catalog, Distribution, Entry, RegionLink, TopicLink, make_id
from datafinder.catalog.schema import SCHEMA_VERSION, SchemaError, connect

from .conftest import NBS


def test_resource_id_is_deterministic_composite_key():
    e = Entry(NBS, "indicator", "6/c1/i1", "工业增加值")
    assert e.resource_id == make_id(NBS, "indicator", "6/c1/i1") == "nbs:indicator:6/c1/i1"


def test_upsert_is_idempotent_and_replaces_relations(cat):
    before = cat.stats()["resources"]
    e = cat.get("nbs:indicator:6/c1/i1")
    e.title = "工业增加值（改名）"
    e.topics = [TopicLink("cnstat.industry")]
    e.distributions = []
    cat.upsert([e, e])  # 重复写入
    again = cat.get(e.resource_id)
    assert cat.stats()["resources"] == before
    assert again.title.endswith("（改名）") and [t.topic_id for t in again.topics] == [
        "cnstat.industry"
    ]
    assert again.distributions == [] and again.delivery == "link"  # 没有分发入口 = 只有链接


def test_hierarchy_children_roots_and_counts(cat):
    assert [r.source_key for r in cat.roots(NBS)] == ["3/c9/i1", "6"]
    kids = cat.children("nbs:dataset:6/c1")
    assert [k.title for k in kids] == ["工业增加值", "第二产业增加值"] and cat.child_count(
        "nbs:dataset:6/c1"
    ) == 2
    assert cat.get("nbs:nope") is None and cat.exists("nbs:database:6")


def test_delivery_is_derived_not_stored(cat):
    assert cat.get("nbs:indicator:6/c1/i1").delivery == "generate"
    assert (
        cat.get("yb.sd:table:2025/14-08").delivery == "download"
    )  # 图片也是"可下载"，usability 另标
    assert cat.get("yb.sd:table:2025/14-08").distributions[0].usability == "image"
    assert cat.get("yb.sd:series:CN-SD").delivery == "link"
    assert not any(
        "delivery" in r[1] for r in cat.conn.execute("PRAGMA table_info(resources)").fetchall()
    )


@pytest.mark.parametrize(
    "bad",
    [
        dict(kind="nonsense"),
        dict(title="  "),
        dict(verification_level="maybe"),
        dict(distributions=[Distribution("telepathy")]),
        dict(distributions=[Distribution("api", usability="vibes")]),
        dict(period_start=2024, period_end=2020),
    ],
)
def test_invalid_entries_are_rejected_before_touching_the_db(cat, bad):
    base = dict(source_id=NBS, kind="indicator", source_key="x", title="t")
    with pytest.raises(ValueError):
        cat.upsert([Entry(**{**base, **bad})])
    assert not cat.exists("nbs:indicator:x")


def test_unknown_topic_or_region_is_rejected_by_foreign_keys(cat):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        cat.upsert([Entry(NBS, "indicator", "z", "t", topics=[TopicLink("cnstat.nope")])])
    with pytest.raises(sqlite3.IntegrityError):
        cat.upsert([Entry(NBS, "indicator", "z", "t", regions=[RegionLink("ZZ-NOPE")])])


def test_dimensions_are_synced_from_taxonomy_and_regions(cat):
    assert (
        cat.conn.execute(
            "SELECT COUNT(*) FROM topics WHERE domain_id='official_statistics_cn'"
        ).fetchone()[0]
        >= 27
    )
    he = cat.conn.execute("SELECT * FROM regions WHERE region_id='CN-HE'").fetchone()
    assert he["parent_id"] == "CN" and "130000000000" in he["codes"]


def test_stats_runs_and_delete_source(cat):
    s = cat.stats()
    assert s["by_kind"]["indicator"] == 3 and s["sources"]["nbs"]["resources"] == 5
    run = cat.start_run(NBS, "discover")
    cat.finish_run(run, "ok", seen=5, changed=2)
    assert cat.conn.execute("SELECT status, seen FROM ingest_runs").fetchone()[:] == ("ok", 5)
    assert cat.delete_source(NBS) == 5 and cat.stats()["sources"]["nbs"]["resources"] == 0
    assert (
        cat.conn.execute(
            "SELECT COUNT(*) FROM resource_topics WHERE resource_id LIKE 'nbs:%'"
        ).fetchone()[0]
        == 0
    )


def test_schema_version_guard(tmp_path):
    p = tmp_path / "old.db"
    conn = connect(p)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaError, match="重建"):
        connect(p)
    assert isinstance(Catalog.open(tmp_path / "fresh.db"), Catalog)
