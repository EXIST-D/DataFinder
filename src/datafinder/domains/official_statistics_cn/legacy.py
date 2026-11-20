"""一次性迁移：把旧版本地索引（nbs_index.db、yearbook_index.db）导入统一目录，免去重爬（国家统计局约 1.5 小时）。

迁移与实时抓取共用同一批构造函数（nbs/entries.py、yearbook/entries.py），两条路径产出一致。
迁移完成后旧库不再被任何代码读取；本模块在下个版本删除。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from datafinder.catalog.repository import Catalog
from datafinder.config import data_dir

from .nbs.client import DATABASES
from .nbs.entries import category_entry, database_entry, dataset_entry, indicator_entry
from .yearbook.entries import edition_entry, series_entry, table_entries
from .yearbook.parse import TocEntry
from .yearbook.registry import load_series

NBS_LEGACY = "nbs_index.db"
YEARBOOK_LEGACY = "yearbook_index.db"


def _open(name: str) -> sqlite3.Connection | None:
    path: Path = data_dir() / name
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _retire(name: str) -> None:
    """迁完把旧库改名为 *.migrated：数据留着（以防万一），但不再被读取，也不再提示"有旧索引待迁移"。"""
    path = data_dir() / name
    path.rename(path.with_name(path.name + ".migrated"))


def _nbs_entries(rows: list[sqlite3.Row]) -> Iterator:
    """旧表一行 = 一个指标；按 (库, 数据集) 分组还原 数据库 → 分类 → 数据集 → 指标。"""
    first = rows[0]
    code, cid = first["db"], first["cid"]
    db_name = DATABASES[code][0]
    names = first["path"].split(" › ")
    if names and names[0] == db_name:
        names = names[1:]
    ds = dataset_entry(code, names, cid, first["root_id"])
    inds = [
        indicator_entry(
            ds, {"id": r["ind_id"], "name": r["name"], "unit": r["unit"], "mark": r["mark"]}
        )
        for r in rows
    ]
    ds.extra["indicators_done"] = True
    return ds, names, inds


def migrate_nbs(cat: Catalog) -> dict:
    conn = _open(NBS_LEGACY)
    if conn is None:
        return {"migrated": False, "reason": f"没有找到旧索引 {NBS_LEGACY}"}
    from .nbs.adapter import NbsAdapter

    NbsAdapter().register_sources(cat)
    codes = [r[0] for r in conn.execute("SELECT DISTINCT db FROM ind")]
    cat.upsert(database_entry(c) for c in codes if c in DATABASES)
    seen_cats: set[str] = set()
    datasets = indicators = 0
    group: list[sqlite3.Row] = []

    def flush() -> None:
        nonlocal datasets, indicators
        if not group:
            return
        ds, names, inds = _nbs_entries(group)
        cats = []
        for i in range(1, len(names)):
            e = category_entry(ds.extra["db"], names[:i])
            if e.resource_id not in seen_cats:
                seen_cats.add(e.resource_id)
                cats.append(e)
        cat.upsert([*cats, ds, *inds])
        datasets += 1
        indicators += len(inds)
        group.clear()

    for row in conn.execute("SELECT * FROM ind ORDER BY db, cid"):
        if group and (row["db"], row["cid"]) != (group[0]["db"], group[0]["cid"]):
            flush()
        group.append(row)
    flush()
    conn.close()
    _retire(NBS_LEGACY)
    return {
        "migrated": True,
        "databases": len(codes),
        "categories": len(seen_cats),
        "datasets": datasets,
        "indicators": indicators,
    }


def migrate_yearbook(cat: Catalog) -> dict:
    conn = _open(YEARBOOK_LEGACY)
    if conn is None:
        return {"migrated": False, "reason": f"没有找到旧索引 {YEARBOOK_LEGACY}"}
    from .yearbook.adapter import YearbookAdapter

    YearbookAdapter().register_sources(cat)
    series = {s.id: s for s in load_series()}
    cat.upsert(series_entry(s) for s in series.values())
    editions = tables = 0
    for ed in conn.execute("SELECT * FROM edition WHERE status='ok' ORDER BY series, year"):
        s = series.get(ed["series"])
        if s is None:
            continue
        toc = [
            TocEntry(r["chapter"], r["no"], r["title"], r["url"], r["fmt"], r["kind"])
            for r in conn.execute(
                "SELECT * FROM ytable WHERE series=? AND year=? ORDER BY rowid", (s.id, ed["year"])
            )
        ]
        entries = table_entries(s, ed["year"], toc)
        cat.upsert([edition_entry(s, ed["year"], ed["toc_url"] or ""), *entries])
        editions += 1
        tables += len(entries)
    conn.close()
    _retire(YEARBOOK_LEGACY)
    return {"migrated": True, "editions": editions, "tables": tables}
