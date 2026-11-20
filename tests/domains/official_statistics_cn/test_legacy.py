"""旧版本地索引 → 统一目录的一次性迁移：结构、分类、幂等、旧库改名。"""

import sqlite3

import pytest

from datafinder.catalog import Catalog
from datafinder.catalog.adapters import get_adapter
from datafinder.config import data_dir

CID, ROOT = "c" * 32, "a" * 32


@pytest.fixture
def legacy_nbs():
    path = data_dir() / "nbs_index.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""CREATE TABLE ind (db TEXT, cid TEXT, root_id TEXT, path TEXT, ind_id TEXT, name TEXT, unit TEXT, mark TEXT,
                          PRIMARY KEY (db, cid, ind_id));""")
    conn.executemany(
        "INSERT INTO ind VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                "6",
                CID,
                ROOT,
                "分省年度数据 › 国民经济核算 › 地区生产总值",
                "i1" * 16,
                "工业增加值 (亿元) ",
                "亿元",
                "口径说明",
            ),
            (
                "6",
                CID,
                ROOT,
                "分省年度数据 › 国民经济核算 › 地区生产总值",
                "i2" * 16,
                "第二产业增加值 (亿元)",
                "亿元",
                None,
            ),
            (
                "3",
                "d" * 32,
                ROOT,
                "年度数据 › 工业 › 工业产品产量",
                "i3" * 16,
                "原煤产量 (万吨)",
                "万吨",
                None,
            ),
        ],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def legacy_yearbook():
    path = data_dir() / "yearbook_index.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
      CREATE TABLE edition (series TEXT, year INTEGER, toc_url TEXT, status TEXT, tables INTEGER, fetched_at TEXT, message TEXT);
      CREATE TABLE ytable (series TEXT, year INTEGER, chapter TEXT, no TEXT, title TEXT, norm TEXT, url TEXT, fmt TEXT, kind TEXT);""")
    conn.execute("INSERT INTO edition VALUES ('CN-SD', 2025, 'u', 'ok', 2, '', '')")
    conn.execute("INSERT INTO edition VALUES ('CN-SD', 2016, NULL, 'missing', 0, '', '404')")
    conn.executemany(
        "INSERT INTO ytable VALUES ('CN-SD', 2025, ?, ?, ?, '', ?, ?, ?)",
        [
            ("第十四篇 工业", "14-1", "规模以上工业增加值", "https://x/14-1.jpg", "jpg", "table"),
            ("第十四篇 工业", "14-1", "同表号另一张", "https://x/14-1b.jpg", "jpg", "table"),
            ("第十四篇 工业", "", "简要说明", "https://x/sm.htm", "html", "note"),
        ],
    )
    conn.commit()
    conn.close()
    return path


def test_nbs_legacy_migrates_into_hierarchy_and_retires_old_file(legacy_nbs, tmp_path):
    with Catalog.open(tmp_path / "c.db") as cat:
        out = get_adapter("nbs").migrate(cat)
        assert out == {
            "migrated": True,
            "databases": 2,
            "categories": 2,
            "datasets": 2,
            "indicators": 3,
        }
        ds = cat.get(f"nbs:dataset:6/{CID}")
        assert (
            ds.title == "地区生产总值"
            and ds.parent_id == "nbs:category:6/国民经济核算"
            and ds.extra["indicators_done"]
        )
        ind = cat.get(f"nbs:indicator:6/{CID}/{'i1' * 16}")
        assert ind.title == "工业增加值" and ind.unit == "亿元" and ind.extra["mark"] == "口径说明"
        assert "cnstat.industry.value_added" in {t.topic_id for t in ind.topics}
        assert cat.get("nbs:category:6/国民经济核算").parent_id == "nbs:database:6"
        assert cat.get("nbs:database:3") is not None
        again = get_adapter("nbs").migrate(cat)  # 旧库已改名：再迁一次不会重复，也不会报错
        assert again["migrated"] is False
    assert not legacy_nbs.exists() and legacy_nbs.with_name("nbs_index.db.migrated").exists()
    assert get_adapter("nbs").legacy_present() is False


def test_yearbook_legacy_migrates_editions_and_tables_only(legacy_yearbook, tmp_path):
    with Catalog.open(tmp_path / "c.db") as cat:
        out = get_adapter("yearbook").migrate(cat)
        assert out == {"migrated": True, "editions": 1, "tables": 2}
        assert cat.get("yearbook.CN-SD:edition:2025").edition_year == 2025
        assert not cat.exists("yearbook.CN-SD:edition:2016")  # 当时 404 的版次不收
        ids = sorted(e.resource_id for e in cat.iter_all("yearbook.CN-SD") if e.kind == "table")
        assert ids == [
            "yearbook.CN-SD:table:2025/14-1",
            "yearbook.CN-SD:table:2025/14-1#2",
        ]  # 说明类条目不收；重复表号加后缀
        assert cat.get("yearbook.cn:series:cn") is not None  # 所有系列的导航入口都登记
    assert not legacy_yearbook.exists()


def test_migration_without_legacy_files_says_so_without_failing(tmp_path):
    with Catalog.open(tmp_path / "c.db") as cat:
        for a in ("nbs", "yearbook"):
            r = get_adapter(a).migrate(cat)
            assert r["migrated"] is False and "没有找到旧索引" in r["reason"]
