"""年鉴目录的本地索引（SQLite）与检索。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from datafinder.config import data_dir
from datafinder.query.plan import parse
from datafinder.query.units import balanced_pick, build_units, combine, prefilter_terms, unit_scores

from .parse import norm_title
from .registry import load_series

PER_CHAPTER = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS edition (
  series TEXT NOT NULL, year INTEGER NOT NULL, toc_url TEXT, status TEXT NOT NULL,
  tables INTEGER NOT NULL DEFAULT 0, fetched_at TEXT, message TEXT,
  PRIMARY KEY (series, year)
);
CREATE TABLE IF NOT EXISTS ytable (
  series TEXT NOT NULL, year INTEGER NOT NULL, chapter TEXT NOT NULL, no TEXT NOT NULL,
  title TEXT NOT NULL, norm TEXT NOT NULL, url TEXT NOT NULL, fmt TEXT NOT NULL, kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ytable_series_year ON ytable(series, year);
CREATE INDEX IF NOT EXISTS ytable_norm ON ytable(norm);
"""


def index_path() -> Path:
    return data_dir() / "yearbook_index.db"


def connect_index(path: Path | None = None) -> sqlite3.Connection:
    p = path or index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    names = {s.id: s for s in load_series()}
    per = {}
    for r in conn.execute(
        "SELECT series, COUNT(*) AS editions, SUM(tables) AS tables, MIN(year) AS lo, MAX(year) AS hi"
        " FROM edition WHERE status='ok' GROUP BY series"
    ):
        s = names.get(r["series"])
        per[r["series"]] = {
            "name": s.zh if s else r["series"],
            "editions": r["editions"],
            "tables": r["tables"],
            "years": [r["lo"], r["hi"]],
        }
    return {"series": per, "tables": sum(v["tables"] for v in per.values())}


def coverage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """每个系列：已建目录的年份、缺失/失败的版次、是否可抓取。"""
    got: dict[str, dict[int, sqlite3.Row]] = {}
    for r in conn.execute("SELECT * FROM edition"):
        got.setdefault(r["series"], {})[r["year"]] = r
    out = []
    for s in load_series():
        rows = got.get(s.id, {})
        out.append(
            {
                "series": s.id,
                "name": s.zh,
                "region": s.region,
                "publisher": s.publisher,
                "home": s.home,
                "crawlable": s.crawl,
                "years_ok": sorted(y for y, r in rows.items() if r["status"] == "ok"),
                "years_missing": sorted(y for y, r in rows.items() if r["status"] == "missing"),
                "years_failed": {y: r["message"] for y, r in sorted(rows.items()) if r["status"] == "error"},
                "tables": sum(r["tables"] for r in rows.values()),
            }
        )
    return out


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    region: str | None = None,
    year: int | None = None,
    series: str | None = None,
    fmt: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """按标题（含章节）二元组覆盖度检索，并把不同年份的同一张表归并，给出"有哪些年份可查"。

    region 可写地区码（CN-HE）；未给时，查询里出现的省份会自动限定到该省年鉴 + 全国年鉴。
    """
    plan = parse(query)
    regions = [region] if region else list(plan.regions)
    units = build_units(query, strip=["统计年鉴", "年鉴", "表格"])
    terms = prefilter_terms(units)
    if not units or not terms:
        return []
    names = {s.id: s for s in load_series()}
    like = " OR ".join(["title LIKE ? OR chapter LIKE ?"] * len(terms))
    args: list[Any] = [x for t in terms for x in (f"%{t}%", f"%{t}%")]
    sql = f"SELECT * FROM ytable WHERE kind='table' AND ({like})"
    if series:
        sql += " AND series=?"
        args.append(series)
    elif regions:
        wanted = ["cn", *[r for r in regions if r in names]]
        sql += f" AND series IN ({','.join('?' * len(wanted))})"
        args += wanted
    if year:
        sql += " AND year=?"
        args.append(year)
    if fmt:
        sql += " AND fmt=?"
        args.append(fmt)

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for r in conn.execute(sql, args):
        vec = unit_scores(units, r["title"], r["chapter"])
        score = combine(units, vec)
        if score <= 0:
            continue
        key = (r["series"], r["norm"])
        g = groups.setdefault(
            key,
            {
                "series": r["series"],
                "yearbook": names[r["series"]].zh if r["series"] in names else r["series"],
                "region": names[r["series"]].region if r["series"] in names else None,
                "title": r["title"],
                "chapter": r["chapter"],
                "score": score,
                "vec": vec,
                "editions": [],
            },
        )
        g["score"] = max(g["score"], score)
        g["vec"] = [max(a, b) for a, b in zip(g["vec"], vec, strict=True)]
        g["editions"].append({"year": r["year"], "no": r["no"], "url": r["url"], "fmt": r["fmt"], "title": r["title"]})
    # 排序：标题覆盖度为主；点名了地区时该省年鉴优先；同等条件下新版次优先（数据更新、表名更接近现状）
    def rank_key(g: dict[str, Any]) -> float:
        newest = max(e["year"] for e in g["editions"])
        bonus = 0.15 if (regions and g["region"] in regions) else 0.0
        return g["score"] + bonus + 0.1 * (newest - 2005) / 20

    ranked = sorted(groups.values(), key=lambda g: -rank_key(g))
    # 多样性：同一系列同一章节最多 3 张表，免得一个主题（如"养老保险"）把结果占满
    per_chapter: dict[tuple[str, str], int] = {}
    diverse = []
    for g in ranked:
        k = (g["series"], g["chapter"])
        per_chapter[k] = per_chapter.get(k, 0) + 1
        if per_chapter[k] <= PER_CHAPTER:
            diverse.append(g)
    ranked = balanced_pick(diverse, [g["vec"] for g in diverse], limit)
    for g in ranked:
        g.pop("vec", None)
        g["editions"].sort(key=lambda e: -e["year"])
        g["title"] = g["editions"][0]["title"]  # 用最新版次的写法
        g["years"] = [e["year"] for e in g["editions"]]
        g["score"] = round(g["score"], 3)
    return ranked


def replace_edition(
    conn: sqlite3.Connection,
    series: str,
    year: int,
    *,
    status: str,
    toc_url: str | None,
    entries: list | None = None,
    message: str | None = None,
) -> None:
    conn.execute("DELETE FROM ytable WHERE series=? AND year=?", (series, year))
    entries = entries or []
    conn.executemany(
        "INSERT INTO ytable(series,year,chapter,no,title,norm,url,fmt,kind) VALUES (?,?,?,?,?,?,?,?,?)",
        [(series, year, e.chapter, e.no, e.title, norm_title(e.title), e.url, e.fmt, e.kind) for e in entries],
    )
    n_tables = sum(1 for e in entries if e.kind == "table")
    conn.execute(
        "INSERT OR REPLACE INTO edition(series,year,toc_url,status,tables,fetched_at,message)"
        " VALUES (?,?,?,?,?,datetime('now'),?)",
        (series, year, toc_url, status, n_tables, message),
    )
    conn.commit()


def rebuild_norms(conn: sqlite3.Connection) -> int:
    """规范化规则调整后，按现行规则重算所有表的归并键（不联网）。"""
    rows = conn.execute("SELECT rowid, title FROM ytable").fetchall()
    conn.executemany("UPDATE ytable SET norm=? WHERE rowid=?", [(norm_title(r["title"]), r["rowid"]) for r in rows])
    conn.commit()
    return len(rows)
