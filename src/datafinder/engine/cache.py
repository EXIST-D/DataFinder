"""SQLite 缓存：查询结果 + 来源故障标记。独立文件 cache.db，可随时删除。

- 查询结果：新鲜期内直接返回；来源失败时可降级使用过期副本（标注"来自缓存"）。
- 故障标记：来源不可达/超时/限流时，10 分钟内默认不再请求（用户点名的来源除外）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from ..config import data_dir
from ..models import Dataset

QUERY_TTL = 6 * 3600  # 查询结果新鲜期：6 小时
OUTAGE_REST = 600  # 故障后休息：10 分钟
MAX_ROWS = 2000  # 缓存条数上限，超出时淘汰最旧的

_SCHEMA = """
CREATE TABLE IF NOT EXISTS query_cache (
    key TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outage (
    source TEXT PRIMARY KEY, until REAL NOT NULL, reason TEXT NOT NULL
);
"""


def cache_path() -> Path:
    return data_dir() / "cache.db"


def make_key(source: str, query: str, limit: int) -> str:
    raw = f"v2|{source}|{query}|{limit}"  # 适配器输出结构变化时升版本，让旧缓存失效
    return hashlib.sha1(raw.encode()).hexdigest()


class Cache:
    def __init__(self, path: Path | str | None = None) -> None:
        target = cache_path() if path is None else path
        if isinstance(target, Path):
            target.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(target))
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ── 查询结果 ──
    def get(
        self, key: str, *, max_age: float | None = QUERY_TTL
    ) -> tuple[list[Dataset], float] | None:
        """返回 (数据集列表, 年龄秒)。max_age=None 表示接受任何年龄（降级用）。"""
        row = self._conn.execute(
            "SELECT payload, created_at FROM query_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        age = time.time() - row[1]
        if max_age is not None and age > max_age:
            return None
        try:
            items = [Dataset.from_dict(d) for d in json.loads(row[0])]
        except (ValueError, TypeError, KeyError):  # 损坏的缓存条目：当作没有，并清理
            self._conn.execute("DELETE FROM query_cache WHERE key = ?", (key,))
            self._conn.commit()
            return None
        return items, age

    def put(self, key: str, source: str, items: list[Dataset]) -> None:
        payload = json.dumps([d.to_dict() for d in items], ensure_ascii=False)
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO query_cache(key, source, payload, created_at) VALUES (?,?,?,?)",
                (key, source, payload, time.time()),
            )
            self._conn.execute(
                "DELETE FROM query_cache WHERE key IN ("
                "SELECT key FROM query_cache ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (MAX_ROWS,),
            )

    # ── 故障标记 ──
    def mark_down(self, source: str, reason: str, rest: float = OUTAGE_REST) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO outage(source, until, reason) VALUES (?,?,?)",
                (source, time.time() + rest, reason),
            )

    def clear_down(self, source: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM outage WHERE source = ?", (source,))

    def resting(self, source: str) -> str | None:
        """来源仍在休息期则返回原因，否则 None。"""
        row = self._conn.execute(
            "SELECT until, reason FROM outage WHERE source = ?", (source,)
        ).fetchone()
        if row is None or row[0] <= time.time():
            return None
        return str(row[1])
