"""目录库的读写：来源登记、资源的幂等写入（含主题/地域/分发入口）、层级浏览、统计。

写入以 resource_id 为键，重复写同一资源只会更新；关联表按资源整体替换。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path

from ..query.tokenize import token_text
from . import regions as region_data
from . import taxonomy
from .entities import Distribution, Entry, RegionLink, TopicLink
from .schema import connect

BATCH = 2000


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Catalog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.sync_dimensions()

    @classmethod
    def open(cls, path: Path | str | None = None) -> Catalog:
        return cls(connect(path))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Catalog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── 维度表（主题、地域）：由词典/地域数据同步，幂等 ──
    def sync_dimensions(self) -> None:
        with self.conn:
            for t in taxonomy.topics().values():
                self.conn.execute(
                    "INSERT INTO topics(topic_id,domain_id,parent_id,name_zh,name_en,level,taxonomy_version)"
                    " VALUES (?,?,?,?,?,?,?) ON CONFLICT(topic_id) DO UPDATE SET parent_id=excluded.parent_id,"
                    " name_zh=excluded.name_zh, name_en=excluded.name_en, level=excluded.level,"
                    " taxonomy_version=excluded.taxonomy_version",
                    (
                        t.id,
                        t.domain,
                        t.parent_id,
                        t.name_zh,
                        t.name_en,
                        t.level,
                        taxonomy.version(t.domain),
                    ),
                )
            for r in region_data.regions().values():
                codes = {k: v for k, v in (("gb", r.gb), ("stat12", r.stat12)) if v}
                self.conn.execute(
                    "INSERT INTO regions(region_id,parent_id,level,name_zh,codes) VALUES (?,?,?,?,?)"
                    " ON CONFLICT(region_id) DO UPDATE SET parent_id=excluded.parent_id, level=excluded.level,"
                    " name_zh=excluded.name_zh, codes=excluded.codes",
                    (r.id, r.parent_id, r.level, r.name_zh, json.dumps(codes)),
                )

    # ── 来源 ──
    def upsert_source(
        self,
        source_id: str,
        *,
        domain_id: str,
        adapter_id: str,
        name: str,
        publisher: str | None = None,
        base_url: str | None = None,
        capabilities: Iterable[str] = (),
        access_policy: str = "unknown",
        status: str = "active",
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO sources(source_id,domain_id,adapter_id,name,publisher,base_url,capabilities,access_policy,status)"
                " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET domain_id=excluded.domain_id,"
                " adapter_id=excluded.adapter_id, name=excluded.name, publisher=excluded.publisher,"
                " base_url=excluded.base_url, capabilities=excluded.capabilities,"
                " access_policy=excluded.access_policy, status=excluded.status",
                (
                    source_id,
                    domain_id,
                    adapter_id,
                    name,
                    publisher,
                    base_url,
                    json.dumps(sorted(capabilities)),
                    access_policy,
                    status,
                ),
            )

    # ── 资源写入 ──
    def upsert(self, entries: Iterable[Entry]) -> int:
        """幂等写入；返回写入条数。按批提交，中途失败不影响已提交的批次。"""
        it = iter(entries)
        total = 0
        while batch := list(islice(it, BATCH)):
            with self.conn:
                for e in batch:
                    self._write(e)
            total += len(batch)
        return total

    def _write(self, e: Entry) -> None:
        e.validate()
        rid = e.resource_id
        self.conn.execute(
            "INSERT INTO resources(resource_id,source_id,parent_id,source_key,kind,title,path,unit,landing_url,"
            "edition_year,period_start,period_end,frequency,verification_level,access_status,license,indexed_at,extra)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(resource_id) DO UPDATE SET parent_id=excluded.parent_id, title=excluded.title,"
            " path=excluded.path, unit=excluded.unit, landing_url=excluded.landing_url,"
            " edition_year=excluded.edition_year, period_start=excluded.period_start,"
            " period_end=excluded.period_end, frequency=excluded.frequency,"
            " verification_level=excluded.verification_level, access_status=excluded.access_status,"
            " license=excluded.license, indexed_at=excluded.indexed_at, extra=excluded.extra",
            (
                rid,
                e.source_id,
                e.parent_id,
                e.source_key,
                e.kind,
                e.title,
                e.path,
                e.unit,
                e.landing_url,
                e.edition_year,
                e.period_start,
                e.period_end,
                e.frequency,
                e.verification_level,
                e.access_status,
                e.license,
                _now(),
                json.dumps(e.extra, ensure_ascii=False),
            ),
        )
        rowid = self.conn.execute(
            "SELECT rowid FROM resources WHERE resource_id=?", (rid,)
        ).fetchone()[0]
        self.conn.execute(
            "INSERT OR REPLACE INTO resources_fts(rowid, tok) VALUES (?, ?)",
            (rowid, token_text(f"{e.title} {e.path or ''}")),
        )
        for table in ("resource_topics", "resource_regions", "distributions"):
            self.conn.execute(f"DELETE FROM {table} WHERE resource_id=?", (rid,))
        self.conn.executemany(
            "INSERT OR REPLACE INTO resource_topics(resource_id,topic_id,method,confidence) VALUES (?,?,?,?)",
            [(rid, t.topic_id, t.method, t.confidence) for t in e.topics],
        )
        self.conn.executemany(
            "INSERT OR REPLACE INTO resource_regions(resource_id,region_id,relation,granularity,scope_mode,evidence_status)"
            " VALUES (?,?,?,?,?,?)",
            [
                (rid, r.region_id, r.relation, r.granularity, r.scope_mode, r.evidence_status)
                for r in e.regions
            ],
        )
        self.conn.executemany(
            "INSERT INTO distributions(resource_id,access_method,url,file_format,usability,access_status,note)"
            " VALUES (?,?,?,?,?,?,?)",
            [
                (rid, d.access_method, d.url, d.file_format, d.usability, d.access_status, d.note)
                for d in e.distributions
            ],
        )

    def delete_source(self, source_id: str) -> int:
        with self.conn:
            self.conn.execute(
                "DELETE FROM resources_fts WHERE rowid IN (SELECT rowid FROM resources WHERE source_id=?)",
                (source_id,),
            )
            return self.conn.execute(
                "DELETE FROM resources WHERE source_id=?", (source_id,)
            ).rowcount

    def delete_children(self, parent_id: str) -> int:
        """删除某资源的直接子资源（重抓一个版次/数据集前先清掉旧子项）。"""
        with self.conn:
            self.conn.execute(
                "DELETE FROM resources_fts WHERE rowid IN (SELECT rowid FROM resources WHERE parent_id=?)",
                (parent_id,),
            )
            return self.conn.execute(
                "DELETE FROM resources WHERE parent_id=?", (parent_id,)
            ).rowcount

    # ── 读取 ──
    def get(self, resource_id: str) -> Entry | None:
        row = self.conn.execute(
            "SELECT * FROM resources WHERE resource_id=?", (resource_id,)
        ).fetchone()
        return self._hydrate([row])[0] if row else None

    def exists(self, resource_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM resources WHERE resource_id=?", (resource_id,)
            ).fetchone()
            is not None
        )

    def children(
        self, parent_id: str | None, *, kind: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[Entry]:
        sql, args = "SELECT * FROM resources WHERE parent_id IS ?", [parent_id]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY source_key LIMIT ? OFFSET ?"
        rows = self.conn.execute(sql, [*args, limit, offset]).fetchall()
        return self._hydrate(rows)

    def child_count(self, parent_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM resources WHERE parent_id=?", (parent_id,)
        ).fetchone()[0]

    def roots(self, source_id: str | None = None) -> list[Entry]:
        sql, args = "SELECT * FROM resources WHERE parent_id IS NULL", []
        if source_id:
            sql += " AND source_id=?"
            args.append(source_id)
        return self._hydrate(
            self.conn.execute(sql + " ORDER BY source_id, source_key", args).fetchall()
        )

    def hydrate_rows(self, rows: list[sqlite3.Row]) -> list[Entry]:
        return self._hydrate(rows)

    def _hydrate(self, rows: list[sqlite3.Row]) -> list[Entry]:
        if not rows:
            return []
        ids = [r["resource_id"] for r in rows]
        marks = ",".join("?" * len(ids))
        topics: dict[str, list[TopicLink]] = {}
        for r in self.conn.execute(
            f"SELECT * FROM resource_topics WHERE resource_id IN ({marks})", ids
        ):
            topics.setdefault(r["resource_id"], []).append(
                TopicLink(r["topic_id"], r["method"], r["confidence"])
            )
        regs: dict[str, list[RegionLink]] = {}
        for r in self.conn.execute(
            f"SELECT * FROM resource_regions WHERE resource_id IN ({marks})", ids
        ):
            regs.setdefault(r["resource_id"], []).append(
                RegionLink(
                    r["region_id"],
                    r["relation"],
                    r["granularity"],
                    r["scope_mode"],
                    r["evidence_status"],
                )
            )
        dists: dict[str, list[Distribution]] = {}
        for r in self.conn.execute(
            f"SELECT * FROM distributions WHERE resource_id IN ({marks}) ORDER BY distribution_id",
            ids,
        ):
            dists.setdefault(r["resource_id"], []).append(
                Distribution(
                    r["access_method"],
                    r["url"],
                    r["file_format"],
                    r["usability"],
                    r["access_status"],
                    r["note"],
                )
            )
        return [
            Entry(
                source_id=r["source_id"],
                kind=r["kind"],
                source_key=r["source_key"],
                title=r["title"],
                parent_id=r["parent_id"],
                path=r["path"],
                unit=r["unit"],
                landing_url=r["landing_url"],
                edition_year=r["edition_year"],
                period_start=r["period_start"],
                period_end=r["period_end"],
                frequency=r["frequency"],
                verification_level=r["verification_level"],
                access_status=r["access_status"],
                license=r["license"],
                extra=json.loads(r["extra"] or "{}"),
                topics=topics.get(r["resource_id"], []),
                regions=regs.get(r["resource_id"], []),
                distributions=dists.get(r["resource_id"], []),
            )
            for r in rows
        ]

    # ── 统计与采集记录 ──
    def stats(self) -> dict:
        per_source = {
            r["source_id"]: {"name": r["name"], "domain": r["domain_id"], "resources": r["n"]}
            for r in self.conn.execute(
                "SELECT s.source_id, s.name, s.domain_id, COUNT(r.resource_id) AS n FROM sources s"
                " LEFT JOIN resources r ON r.source_id=s.source_id GROUP BY s.source_id"
            )
        }
        by_kind = {
            r["kind"]: r["n"]
            for r in self.conn.execute("SELECT kind, COUNT(*) AS n FROM resources GROUP BY kind")
        }
        last = self.conn.execute("SELECT MAX(indexed_at) FROM resources").fetchone()[0]
        return {
            "resources": sum(by_kind.values()),
            "by_kind": by_kind,
            "sources": per_source,
            "last_indexed": last,
        }

    def start_run(self, source_id: str, operation: str) -> int:
        with self.conn:
            return self.conn.execute(
                "INSERT INTO ingest_runs(source_id,operation,started_at,status) VALUES (?,?,?,'running')",
                (source_id, operation, _now()),
            ).lastrowid  # type: ignore[return-value]

    def finish_run(
        self,
        run_id: int,
        status: str,
        *,
        seen: int = 0,
        changed: int = 0,
        message: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE ingest_runs SET finished_at=?, status=?, seen=?, changed=?, message=? WHERE run_id=?",
                (_now(), status, seen, changed, message, run_id),
            )

    def iter_all(self, source_id: str | None = None, batch: int = 1000) -> Iterator[Entry]:
        sql, args = "SELECT * FROM resources", []
        if source_id:
            sql += " WHERE source_id=?"
            args.append(source_id)
        cur = self.conn.execute(sql, args)
        while rows := cur.fetchmany(batch):
            yield from self._hydrate(rows)
