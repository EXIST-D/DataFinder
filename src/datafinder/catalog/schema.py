"""统一资源目录的 SQLite 表结构（带版本）。

只建今天能填真数据的字段：来源、资源、主题、地域、分发入口、采集记录。
证据先用 verification_level + checked_at + extra 承接；覆盖验证在取数结果里算，不存目录。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import data_dir

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS sources (
  source_id    TEXT PRIMARY KEY,
  domain_id    TEXT NOT NULL,
  adapter_id   TEXT NOT NULL,
  name         TEXT NOT NULL,
  publisher    TEXT,
  base_url     TEXT,
  capabilities TEXT NOT NULL DEFAULT '[]',   -- JSON：discover / search / inspect / fetch / download
  access_policy TEXT NOT NULL DEFAULT 'unknown',
  status       TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS resources (
  resource_id  TEXT PRIMARY KEY,             -- {source_id}:{kind}:{source_key}，确定性，可随时重建
  source_id    TEXT NOT NULL REFERENCES sources(source_id),
  parent_id    TEXT,
  source_key   TEXT NOT NULL,
  kind         TEXT NOT NULL,
  title        TEXT NOT NULL,
  path         TEXT,                          -- 所在分类/章节路径（供检索与展示）
  unit         TEXT,
  landing_url  TEXT,
  edition_year INTEGER,                       -- 出版年（年鉴版次）
  period_start INTEGER,                       -- 观测期起止（年）；start 有值、end 为空 = 至今
  period_end   INTEGER,
  frequency    TEXT,                          -- annual / quarterly / monthly / ...
  verification_level TEXT NOT NULL DEFAULT 'unverified',
  access_status TEXT NOT NULL DEFAULT 'unknown',
  license      TEXT,
  indexed_at   TEXT NOT NULL,
  checked_at   TEXT,
  extra        TEXT NOT NULL DEFAULT '{}',    -- JSON：适配器私有字段（upstream 码、证据摘要…）
  UNIQUE (source_id, kind, source_key)
);
CREATE INDEX IF NOT EXISTS idx_res_parent ON resources(parent_id);
CREATE INDEX IF NOT EXISTS idx_res_kind ON resources(kind, source_id);
CREATE INDEX IF NOT EXISTS idx_res_title ON resources(title);

-- 全文索引：标题+路径经二元组分词后存入（与 query.tokenize 同一套），查询时把关键词变成"相邻词组"，等价于子串匹配
CREATE VIRTUAL TABLE IF NOT EXISTS resources_fts USING fts5(tok, tokenize = 'unicode61 remove_diacritics 0');

CREATE TABLE IF NOT EXISTS topics (
  topic_id  TEXT PRIMARY KEY,
  domain_id TEXT NOT NULL,
  parent_id TEXT,
  name_zh   TEXT NOT NULL,
  name_en   TEXT,
  level     INTEGER NOT NULL,
  taxonomy_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_topics (
  resource_id TEXT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
  topic_id    TEXT NOT NULL REFERENCES topics(topic_id),
  method      TEXT NOT NULL,                  -- rule / inferred / manual
  confidence  REAL,
  PRIMARY KEY (resource_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_rt_topic ON resource_topics(topic_id, resource_id);

CREATE TABLE IF NOT EXISTS regions (
  region_id TEXT PRIMARY KEY,
  parent_id TEXT,
  level     TEXT NOT NULL,
  name_zh   TEXT NOT NULL,
  codes     TEXT NOT NULL DEFAULT '{}'        -- JSON：{"gb": "...", "stat12": "..."}
);

CREATE TABLE IF NOT EXISTS resource_regions (
  resource_id TEXT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
  region_id   TEXT NOT NULL REFERENCES regions(region_id),
  relation    TEXT NOT NULL,                  -- covers（数据覆盖）/ reports_at（按该级别报告）
  granularity TEXT,                           -- country / province / prefecture / county
  scope_mode  TEXT NOT NULL DEFAULT 'unknown',-- exact / descendants / aggregate / unknown
  evidence_status TEXT NOT NULL DEFAULT 'inferred',  -- structure（来源结构决定）/ inferred（标题推断）/ verified（取数验证）
  PRIMARY KEY (resource_id, region_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_rr_region ON resource_regions(region_id, resource_id);

CREATE TABLE IF NOT EXISTS distributions (
  distribution_id INTEGER PRIMARY KEY AUTOINCREMENT,
  resource_id   TEXT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
  access_method TEXT NOT NULL,                -- api / direct_file / html_parse / page / manual
  url           TEXT,
  file_format   TEXT,
  usability     TEXT,                         -- tabular / html_table / document / image
  access_status TEXT NOT NULL DEFAULT 'unknown',
  note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_dist_res ON distributions(resource_id, access_method);

CREATE TABLE IF NOT EXISTS ingest_runs (
  run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id   TEXT NOT NULL,
  operation   TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT NOT NULL,                  -- running / ok / paused / error
  seen        INTEGER NOT NULL DEFAULT 0,
  changed     INTEGER NOT NULL DEFAULT 0,
  message     TEXT
);
"""


def db_path() -> Path:
    return data_dir() / "catalog.db"


class SchemaError(RuntimeError):
    pass


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = db_path() if path is None else Path(path) if not str(path).startswith(":") else path
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        conn.close()
        raise SchemaError(
            f"目录库版本 {version} 与程序要求的 {SCHEMA_VERSION} 不一致。目录可随时重建：删除 {target} 后重新建立索引。"
        )
    conn.executescript(DDL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn
