"""SQLite 存储：门户表 + 全文索引 + 元信息。单文件，无需服务。

门户表是"导出数据"，可随时由 `datafinder sync` 重建。所以表结构变了不做迁移，
直接按 user_version 丢弃重建，再提示用户重新 sync。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import data_dir

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS portals (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    name_zh       TEXT,
    url           TEXT NOT NULL,
    norm_url      TEXT NOT NULL,
    org           TEXT,
    country       TEXT,
    country_en    TEXT,
    region        TEXT,
    region_zh     TEXT,
    catalog_type  TEXT,
    owner_type    TEXT,
    tier          INTEGER,            -- 机构类型档位 1–6，见 data/facets.yaml
    kind          TEXT NOT NULL DEFAULT 'other',   -- 数据形态（7 类）
    openness      TEXT NOT NULL DEFAULT 'open',    -- open / mixed / restricted
    labels        TEXT NOT NULL DEFAULT '[]',      -- 专区标签，如 stats_bureau / open_platform
    note          TEXT,               -- 获取方式等备注（如"需注册获取 userKey"）
    verify        TEXT,               -- 自有精选的核验状态：ok / blocked / unverified
    software      TEXT,
    api_url       TEXT,
    has_api       INTEGER NOT NULL DEFAULT 0,
    access        TEXT,
    industries    TEXT NOT NULL DEFAULT '[]',
    topics        TEXT NOT NULL DEFAULT '[]',
    description   TEXT,
    desc_zh       TEXT,
    is_national   INTEGER NOT NULL DEFAULT 0,
    featured      INTEGER NOT NULL DEFAULT 0,
    curated       INTEGER NOT NULL DEFAULT 0,
    source        TEXT NOT NULL,
    tokens        TEXT NOT NULL,
    imported_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS portals_country ON portals(country);
CREATE INDEX IF NOT EXISTS portals_region ON portals(region);
CREATE INDEX IF NOT EXISTS portals_featured ON portals(featured);
CREATE INDEX IF NOT EXISTS portals_tier ON portals(tier);
CREATE INDEX IF NOT EXISTS portals_kind ON portals(kind);
CREATE TABLE IF NOT EXISTS portal_industries (
    portal_rowid INTEGER NOT NULL, industry TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS portal_industries_ind ON portal_industries(industry, portal_rowid);
CREATE INDEX IF NOT EXISTS portals_norm_url ON portals(norm_url);
CREATE VIRTUAL TABLE IF NOT EXISTS portals_fts USING fts5(
    name, name_zh, description, hints,
    tokenize = 'unicode61 remove_diacritics 0'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_DROP = (
    "DROP TABLE IF EXISTS portals; DROP TABLE IF EXISTS portals_fts; "
    "DROP TABLE IF EXISTS portal_industries; DROP TABLE IF EXISTS meta;"
)


def db_path() -> Path:
    return data_dir() / "datafinder.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target: Path | str = db_path() if path is None else path
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        conn.executescript(_DROP)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.executescript(SCHEMA)
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def portal_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM portals").fetchone()[0]
