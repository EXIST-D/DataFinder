"""数据来源（门户目录）：把门户库里的网站与目录里已索引的来源关联起来，说明"接入到什么程度"。

门户 ≠ 数据资源：门户库只有入口网址，目录里才有资源级索引。两者在不同的 SQLite 文件里，不建外键，
用**规范化网址**当稳定键关联：
- 自动：门户网址与来源的 base_url（或其首页条目网址）规范化后完全一致 → same_site；
- 例外：领域包登记的人工认定（catalog.portal_links），relation 为 hosted_on / section / related。
  related 只作提示，不计入接入等级；不同网站不会因为域名后缀相同而被合并。
接入等级（UI 投影，不替代底层能力）：directory_only 仅入口 → indexed 已有资源级索引 → indexed_and_fetchable 有可取数的接口/解析。
已索引资源数永远从目录实算，不手工维护；它不等于原站全部数据。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from datafinder.catalog import SearchRequest, kinds, regions
from datafinder.catalog.adapters import adapters
from datafinder.catalog.portal_links import overrides
from datafinder.catalog.repository import Catalog
from datafinder.catalog.tree import tree_children
from datafinder.portals.browse import Filters, browse, portal_dict
from datafinder.store import connect, portal_count
from datafinder.util import normalize_url

from .common import open_catalog
from .errors import ServiceError, guard

LEVELS = ("directory_only", "indexed", "indexed_and_fetchable")
LEVEL_ZH = {
    "directory_only": "仅门户入口",
    "indexed": "已有资源目录",
    "indexed_and_fetchable": "可取数",
}
COUNTED = ("same_site", "hosted_on", "section")  # 计入接入等级的关系
SOURCE_ENTRY = "source:"  # 目录里有、门户库里没有对应记录的来源，用 source:<id> 当条目 id
PAGE_MAX = 50


@dataclass
class _Source:
    source_id: str
    name: str
    domain_id: str
    base_url: str | None
    capabilities: list[str]
    resources: int
    last_indexed_at: str | None
    region_ids: list[str] = field(default_factory=list)


def _sources(cat: Catalog) -> dict[str, _Source]:
    caps = {a.id: set(a.capabilities) for a in adapters().values()}
    searchable = kinds.searchable_kinds()
    marks = ",".join("?" * len(searchable))
    stats = {
        r["source_id"]: (r["n"], r["last"])
        for r in cat.conn.execute(
            f"SELECT source_id, COUNT(*) AS n, MAX(indexed_at) AS last FROM resources"
            f" WHERE kind IN ({marks}) GROUP BY source_id",
            searchable,
        )
    }
    # 能力以目录里的实际分发入口为准（有"生成"类入口才算可取数，有直链才算可下载），而不是适配器声明
    evidence: dict[str, set[str]] = {}
    delivery_of = {m: v["delivery"] for m, v in kinds.access_methods().items()}
    for r in cat.conn.execute(
        "SELECT DISTINCT r.source_id AS s, d.access_method AS m FROM distributions d"
        " JOIN resources r ON r.resource_id = d.resource_id"
    ):
        got = delivery_of.get(r["m"])
        if got in ("generate", "download"):
            evidence.setdefault(r["s"], set()).add("fetch" if got == "generate" else "download")
    regs: dict[str, list[str]] = {}
    for r in cat.conn.execute(
        "SELECT DISTINCT r.source_id AS s, rr.region_id AS g FROM resources r"
        " JOIN resource_regions rr ON rr.resource_id = r.resource_id"
        " WHERE r.parent_id IS NULL AND rr.relation = 'covers'"
    ):
        regs.setdefault(r["s"], []).append(r["g"])
    out: dict[str, _Source] = {}
    for r in cat.conn.execute("SELECT * FROM sources ORDER BY source_id"):
        n, last = stats.get(r["source_id"], (0, None))
        declared = (
            set(json.loads(r["capabilities"] or "[]")) | caps.get(r["adapter_id"], set())
        ) - {"fetch"}
        out[r["source_id"]] = _Source(
            source_id=r["source_id"],
            name=r["name"],
            domain_id=r["domain_id"],
            base_url=r["base_url"],
            capabilities=sorted(declared | evidence.get(r["source_id"], set())),
            resources=n,
            last_indexed_at=last,
            region_ids=sorted(regs.get(r["source_id"], [])),
        )
    return out


def _link_keys(cat: Catalog, sources: dict[str, _Source]) -> list[tuple[str, str, str, str]]:
    """(规范化网址, source_id, relation, method)：自动关联 + 人工认定。"""
    keys: list[tuple[str, str, str, str]] = []
    homes = {
        r["source_id"]: r["landing_url"]
        for r in cat.conn.execute(
            "SELECT source_id, landing_url FROM resources WHERE kind = 'portal' AND source_key = 'home'"
        )
    }
    for s in sources.values():
        for url in dict.fromkeys(u for u in (s.base_url, homes.get(s.source_id)) if u):
            keys.append((normalize_url(url), s.source_id, "same_site", "url_exact"))
    for link in overrides():
        if link["source_id"] in sources:
            keys.append(
                (normalize_url(link["portal_key"]), link["source_id"], link["relation"], "curated")
            )
    return keys


def _portals_by_url(conn: sqlite3.Connection, wanted: set[str]) -> dict[str, list[sqlite3.Row]]:
    out: dict[str, list[sqlite3.Row]] = {}
    keys = sorted(wanted)
    for i in range(0, len(keys), 400):
        part = keys[i : i + 400]
        for row in conn.execute(
            f"SELECT * FROM portals WHERE norm_url IN ({','.join('?' * len(part))})", part
        ):
            out.setdefault(row["norm_url"], []).append(row)
    return out


class _Directory:
    """一次请求内的关联结果：门户条目（portal_id → 关联）、没有门户记录的来源条目。"""

    def __init__(self, cat: Catalog, conn: sqlite3.Connection | None) -> None:
        self.sources = _sources(cat)
        keys = _link_keys(cat, self.sources)
        by_url = _portals_by_url(conn, {k[0] for k in keys}) if conn else {}
        self.portals: dict[str, sqlite3.Row] = {}
        self.links: dict[str, list[dict[str, Any]]] = {}  # portal_id → 关联
        linked_sources: set[str] = set()
        for url, source_id, relation, method in keys:
            for row in by_url.get(url, []):
                self.portals[row["id"]] = row
                entry = {"source_id": source_id, "relation": relation, "method": method}
                bucket = self.links.setdefault(row["id"], [])
                if entry not in bucket:
                    bucket.append(entry)
                if relation in COUNTED:
                    linked_sources.add(source_id)
        self.source_only = [s for sid, s in self.sources.items() if sid not in linked_sources]

    def entry(self, portal_id: str) -> dict[str, Any]:
        row = self.portals[portal_id]
        return _entry(self, portal_dict(row), self.links.get(portal_id, []))

    def source_entry(self, s: _Source) -> dict[str, Any]:
        item = {
            "id": f"{SOURCE_ENTRY}{s.source_id}",
            "name": s.name,
            "url": s.base_url,
            "official": None,
            "region_code": s.region_ids[0] if len(s.region_ids) == 1 else None,
            "country_code": None,
            "industries": [],
            "verify": None,
            "note": None,
        }
        return _entry(
            self,
            item,
            [{"source_id": s.source_id, "relation": "same_site", "method": "source_only"}],
        )


def _entry(d: _Directory, portal: dict[str, Any], links: list[dict[str, Any]]) -> dict[str, Any]:
    counted = [lk["source_id"] for lk in links if lk["relation"] in COUNTED]
    counted = list(dict.fromkeys(counted))
    srcs = [d.sources[s] for s in counted]
    caps = sorted(
        {c for s in srcs for c in s.capabilities if c in ("discover", "fetch", "download")}
    )
    resources = sum(s.resources for s in srcs)
    if not srcs or resources == 0:
        level = "directory_only"
    elif "fetch" in caps:
        level = "indexed_and_fetchable"
    else:
        level = "indexed"
    last = max((s.last_indexed_at for s in srcs if s.last_indexed_at), default=None)
    nav = f"source:{counted[0]}" if counted else None
    return {
        "portal_id": None if str(portal["id"]).startswith(SOURCE_ENTRY) else portal["id"],
        "id": portal["id"],
        "name": portal["name"],
        "home_url": portal["url"],
        "official": portal.get("official"),
        "country_code": portal.get("country_code"),
        "region_code": portal.get("region_code"),
        "industries": portal.get("industries", []),
        "domains": sorted({s.domain_id for s in srcs}),
        "integration": {
            "level": level,
            "level_zh": LEVEL_ZH[level],
            "linked_source_ids": counted,
            "links": links,
            "indexed_resources": resources,
            "capabilities": caps,
        },
        "catalog_navigation": {"axis": "source", "node": nav} if nav else None,
        "last_indexed_at": last,
        "verification": {"status": portal.get("verify"), "note": portal.get("note")},
    }


_RANK = {lv: i for i, lv in enumerate(LEVELS)}  # 接入越深越靠前


_CACHE: dict[str, Any] = {}


def cached_directory(cat: Catalog, conn: sqlite3.Connection | None) -> _Directory:
    """关联结果的进程内缓存：目录库或门户库有变化（文件时间/大小，含 WAL）才重算。门户树逐层展开时会频繁用到。"""
    from datafinder.config import data_dir

    home = data_dir()
    key = tuple(
        (name, st.st_mtime_ns, st.st_size)
        for name in ("catalog.db", "catalog.db-wal", "datafinder.db", "datafinder.db-wal")
        if (st := (home / name).stat() if (home / name).exists() else None)
    )
    hit = _CACHE.get("directory")
    if hit and hit[0] == key:
        return hit[1]
    d = _Directory(cat, conn)
    _CACHE["directory"] = (key, d)
    return d


def _open_portals() -> sqlite3.Connection | None:
    conn = connect()
    if not portal_count(conn):
        conn.close()
        return None
    return conn


def _match(
    e: dict[str, Any], q: str, domain: str | None, region: str | None, official: bool
) -> bool:
    if q:
        hay = f"{e['name']} {e['home_url'] or ''}".lower()
        if q.lower() not in hay:
            return False
    if domain and domain not in e["domains"] and domain not in e["industries"]:
        return False
    if region:
        wanted = {region, *regions.ancestors(region)}
        if e["region_code"] and e["region_code"] not in wanted:
            return False
    if official and not e["official"]:
        return False
    return True


def directory(
    *,
    scope: str = "indexed",
    level: str | None = None,
    q: str = "",
    domain: str | None = None,
    region: str | None = None,
    country: str | None = None,
    official: bool = False,
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """scope=indexed：已建立资源索引的来源/门户（含门户库里没有记录的来源）；scope=all：门户库全部门户并标注接入等级。"""
    if scope not in ("indexed", "all"):
        raise ServiceError("scope 只能是 indexed 或 all", code="INVALID_ARGUMENT")
    if level and level not in LEVELS:
        raise ServiceError(f"level 只能是 {' / '.join(LEVELS)}", code="INVALID_ARGUMENT")
    size, page = max(1, min(size, PAGE_MAX)), max(1, page)
    if region:
        rid = regions.resolve(region)
        if not rid:
            raise ServiceError(
                f"未知地域 {region!r}",
                code="INVALID_FACET_ID",
                next_action="用 facets 查看合法的地域",
            )
        region = rid
    with guard(), open_catalog() as cat:
        conn = _open_portals()
        try:
            d = _Directory(cat, conn)
            if scope == "indexed":
                items = [d.entry(pid) for pid in d.portals if d.links.get(pid)]
                items = [e for e in items if e["integration"]["level"] != "directory_only"]
                items += [d.source_entry(s) for s in d.source_only if s.resources]
                items = [e for e in items if _match(e, q.strip(), domain, region, official)]
                if level:
                    items = [e for e in items if e["integration"]["level"] == level]
                items.sort(
                    key=lambda e: (
                        -_RANK[e["integration"]["level"]],
                        -e["integration"]["indexed_resources"],
                        e["name"],
                    )
                )
                total = len(items)
                page_items = items[(page - 1) * size : page * size]
            else:
                if conn is None:
                    raise ServiceError(
                        "门户库为空，请先运行 datafinder sync",
                        code="INDEX_NOT_READY",
                        next_action="datafinder sync",
                    )
                f = Filters(domain=domain, country=country, region=region, official=official)
                rows, total = browse(conn, f, page=page, size=size)
                page_items = [
                    d.entry(r["id"]) if r["id"] in d.portals else _plain(d, r) for r in rows
                ]
                if level:
                    page_items = [e for e in page_items if e["integration"]["level"] == level]
        finally:
            if conn:
                conn.close()
    return {
        "scope": scope,
        "total": total,
        "page": page,
        "size": size,
        "items": page_items,
        "levels": LEVEL_ZH,
        "note": "已索引资源数来自本机目录，不等于原站全部数据；门户与资源是不同实体，数量不能相加。",
    }


def _plain(d: _Directory, row: sqlite3.Row) -> dict[str, Any]:
    return _entry(d, portal_dict(row), [])


def detail(entry_id: str) -> dict[str, Any]:
    """门户（或仅目录里有的来源）的详情：接入情况、关联来源、可浏览的根目录、使用说明。"""
    with guard(), open_catalog() as cat:
        conn = _open_portals()
        try:
            d = _Directory(cat, conn)
            if entry_id.startswith(SOURCE_ENTRY):
                s = d.sources.get(entry_id[len(SOURCE_ENTRY) :])
                if s is None:
                    raise ServiceError(f"目录里没有来源 {entry_id!r}", code="NOT_FOUND")
                e = d.source_entry(s)
            else:
                if entry_id not in d.portals:
                    row = (
                        conn.execute("SELECT * FROM portals WHERE id = ?", (entry_id,)).fetchone()
                        if conn
                        else None
                    )
                    if row is None:
                        raise ServiceError(
                            f"门户库里没有 {entry_id!r}",
                            code="NOT_FOUND",
                            next_action="用 /api/data-sources 列表里的 id",
                        )
                    e = _plain(d, row)
                else:
                    e = d.entry(entry_id)
            sources = []
            for sid in e["integration"]["linked_source_ids"]:
                s = d.sources[sid]
                roots = tree_children(
                    cat,
                    SearchRequest(source_ids=[sid]),
                    axis="source",
                    node=f"source:{sid}",
                    limit=12,
                )
                sources.append(
                    {
                        "source_id": sid,
                        "name": s.name,
                        "domain_id": s.domain_id,
                        "resources": s.resources,
                        "capabilities": s.capabilities,
                        "last_indexed_at": s.last_indexed_at,
                        "roots": [
                            {"id": c["id"], "name": c["name"], "count": c["count"]}
                            for c in roots["children"]
                        ],
                    }
                )
            e["sources"] = sources
            e["related"] = [lk for lk in e["integration"]["links"] if lk["relation"] == "related"]
            e["usage"] = _usage(e)
            return e
        finally:
            if conn:
                conn.close()


def _usage(e: dict[str, Any]) -> list[str]:
    caps, level = e["integration"]["capabilities"], e["integration"]["level"]
    notes = []
    if level == "directory_only":
        notes.append(
            "DataFinder 目前只有这个入口网址，没有资源级索引：请在浏览器打开原站查找数据。"
        )
    if "fetch" in caps:
        notes.append("目录里有可实时取数的资源（用 fetch）；不代表该站所有表都能自动取数。")
    if level in ("indexed", "indexed_and_fetchable"):
        notes.append("资源只收录名称、口径与链接，不含数值；可下载的文件直链用 download。")
    if e["verification"]["status"] == "blocked":
        notes.append("该站拒绝自动访问，需要用浏览器打开。")
    if e["verification"]["note"]:
        notes.append(str(e["verification"]["note"]))
    return notes


def portal_for_source(cat: Catalog, source_id: str) -> dict[str, Any] | None:
    """某个来源对应的门户条目（供资源详情"回到门户"）；门户库没同步时只给来源条目。"""
    conn = _open_portals()
    try:
        d = _Directory(cat, conn)
        for pid, links in d.links.items():
            if any(lk["source_id"] == source_id and lk["relation"] in COUNTED for lk in links):
                e = d.entry(pid)
                return {
                    "id": e["id"],
                    "name": e["name"],
                    "home_url": e["home_url"],
                    "level": e["integration"]["level"],
                }
        s = d.sources.get(source_id)
        if s is None:
            return None
        return {
            "id": f"{SOURCE_ENTRY}{source_id}",
            "name": s.name,
            "home_url": s.base_url,
            "level": d.source_entry(s)["integration"]["level"],
        }
    finally:
        if conn:
            conn.close()
