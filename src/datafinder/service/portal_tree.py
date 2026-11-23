"""门户树：全部数据门户按 国家 → 地区 → 机构类型 → 门户 逐层展开；已接入的门户还能继续展开到它的来源与资源目录。

和"目录树"（资源按主题/地域/来源）互补：这棵树以**门户（网站）**为主体，门户库里的 4 万多个门户全部在里面，
其中与目录来源关联上的（见 portal_directory）可以一路点开到具体资源。节点按需加载，一次只返回一层。

节点 id（不透明，前端原样回传）：
  c:<国家>            国家/地区          r:<国家>:<地区>        国家下的地区（省、州…）
  o:<国家>            目录里有、门户库里没有对应记录的来源（只在国家下出现）
  t:<国家>:<地区>:<档>  机构类型分组        p:<门户 id>           门户（有关联来源时可展开）
  k:<目录节点 id>      目录里的来源/资源（沿用目录树的节点 id）
小分组（门户数 ≤ FLAT）不再拆机构类型，直接列门户，避免"点开只有一两项"的空层级。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from datafinder.catalog import SearchRequest, regions
from datafinder.catalog.repository import Catalog
from datafinder.catalog.tree import tree_children
from datafinder.portals.browse import _ORDER, country_zh_name, portal_dict
from datafinder.portals.facets import load_facets
from datafinder.portals.search import search_portals

from . import portal_directory as pd
from .common import open_catalog
from .errors import ServiceError, guard

FLAT = 15  # 一个分组的门户数不超过它就直接列出
LIMIT_MAX = 300
WORLD = "World"
_LEAF_ORDER = _ORDER["official"]


def _tier_name(tier: int) -> str:
    for t in load_facets().tiers:
        if t.tier == tier:
            return t.zh
    return "未分类"


def _country_name(code: str) -> str:
    if not code:
        return "未标注国家/地区"
    return country_zh_name(code) or code


def _region_name(country: str, region: str) -> str:
    if not region:
        return "（国家级）"
    r = regions.get(region)
    return r.name_zh if r else region.removeprefix(f"{country}-")


def _country_of_region(rid: str) -> str:
    r = regions.get(rid)
    while r is not None and r.level != "country":
        r = regions.get(r.parent_id) if r.parent_id else None
    return r.id if r else ""


def _node(id_: str, kind: str, name: str, **kw: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "kind": kind,
        "name": name,
        "has_children": kw.pop("has_children", False),
        **kw,
    }


class _Ctx:
    """一次请求内共用的东西：目录关联、门户库连接、"只看已接入"的过滤条件。"""

    def __init__(self, cat: Catalog, conn: sqlite3.Connection, indexed_only: bool) -> None:
        self.cat, self.conn = cat, conn
        self.d = pd.cached_directory(cat, conn)
        self.indexed_ids = {
            pid
            for pid in self.d.portals
            if self.d.entry(pid)["integration"]["level"] != "directory_only"
        }
        self.only = indexed_only
        # 目录里有、门户库里没有记录的来源，按它覆盖的地域归到国家
        self.orphans: dict[str, list[Any]] = {}
        for s in self.d.source_only:
            if s.resources:
                country = next((c for rid in s.region_ids if (c := _country_of_region(rid))), "")
                self.orphans.setdefault(country, []).append(s)

    def where(self, extra: str = "", args: list | None = None) -> tuple[str, list]:
        parts, a = [], list(args or [])
        if extra:
            parts.append(extra)
        if self.only:
            ids = sorted(self.indexed_ids)
            parts.append(f"p.id IN ({','.join('?' * len(ids))})" if ids else "0")
            a += ids
        return (" AND ".join(parts) or "1=1"), a

    def portal(self, row: sqlite3.Row) -> dict[str, Any]:
        e = self.d.entry(row["id"]) if row["id"] in self.d.portals else pd._plain(self.d, row)
        info = portal_dict(row)
        integ = e["integration"]
        return _node(
            f"p:{row['id']}",
            "portal",
            info["name"],
            portal_id=row["id"],
            home_url=info["url"],
            official=info["official"],
            org_type=info["org_type"],
            data_kind=info["kind"],
            api=info["api"],
            openness=info["openness"],
            verify=info["verify"],
            description=info["description"],
            level=integ["level"],
            level_zh=integ["level_zh"],
            indexed_resources=integ["indexed_resources"],
            has_children=bool(integ["linked_source_ids"]) and integ["indexed_resources"] > 0,
        )

    def source_node(self, sid: str) -> dict[str, Any]:
        s = self.d.sources[sid]
        return _node(
            f"k:source:{sid}",
            "source",
            s.name,
            count=s.resources,
            source_id=sid,
            capabilities=s.capabilities,
            has_children=s.resources > 0,
        )


def _portal_rows(
    ctx: _Ctx,
    country: str,
    region: str | None,
    tier: int | None,
    limit: int,
    offset: int,
    focus: str | None,
) -> tuple[list[sqlite3.Row], int, int]:
    clause = "COALESCE(p.country,'') = ?"
    args: list = [country]
    if region is not None:
        clause += " AND COALESCE(p.region,'') = ?"
        args.append(region)
    if tier is not None:
        clause += " AND COALESCE(p.tier,0) = ?"
        args.append(tier)
    where, a = ctx.where(clause, args)
    total = ctx.conn.execute(f"SELECT COUNT(*) FROM portals p WHERE {where}", a).fetchone()[0]
    if focus:  # 定位：翻到包含该门户的那一页
        ids = [
            r[0]
            for r in ctx.conn.execute(
                f"SELECT p.id FROM portals p WHERE {where} ORDER BY {_LEAF_ORDER}", a
            )
        ]
        if focus in ids:
            offset = (ids.index(focus) // limit) * limit
    rows = ctx.conn.execute(
        f"SELECT p.* FROM portals p WHERE {where} ORDER BY {_LEAF_ORDER} LIMIT ? OFFSET ?",
        [*a, limit, offset],
    ).fetchall()
    return rows, total, offset


def _orphan_nodes(ctx: _Ctx, country: str) -> list[dict[str, Any]]:
    return [ctx.source_node(s.source_id) for s in ctx.orphans.get(country, [])]


def _tier_groups(
    ctx: _Ctx, country: str, region: str, counts: dict[int, int]
) -> list[dict[str, Any]]:
    return [
        _node(
            f"t:{country}:{region}:{tr}",
            "tier",
            _tier_name(tr),
            count=n,
            has_children=True,
        )
        for tr, n in sorted(counts.items(), key=lambda x: (x[0] == 0, x[0]))
    ]


def _group_counts(ctx: _Ctx, country: str) -> dict[str, dict[int, int]]:
    where, a = ctx.where("COALESCE(p.country,'') = ?", [country])
    out: dict[str, dict[int, int]] = {}
    for r in ctx.conn.execute(
        f"SELECT COALESCE(p.region,'') rg, COALESCE(p.tier,0) tr, COUNT(*) n FROM portals p WHERE {where} GROUP BY 1, 2",
        a,
    ):
        out.setdefault(r["rg"], {})[r["tr"]] = r["n"]
    return out


def _root(ctx: _Ctx) -> list[dict[str, Any]]:
    where, a = ctx.where()
    counts = {
        r["c"]: r["n"]
        for r in ctx.conn.execute(
            f"SELECT COALESCE(p.country,'') c, COUNT(*) n FROM portals p WHERE {where} GROUP BY 1",
            a,
        )
    }
    indexed_by_country: dict[str, int] = {}
    if ctx.indexed_ids:
        marks = ",".join("?" * len(ctx.indexed_ids))
        for r in ctx.conn.execute(
            f"SELECT COALESCE(country,'') c, COUNT(*) n FROM portals WHERE id IN ({marks}) GROUP BY 1",
            sorted(ctx.indexed_ids),
        ):
            indexed_by_country[r["c"]] = r["n"]
    for c, ss in ctx.orphans.items():
        counts[c] = counts.get(c, 0) + len(ss)
        indexed_by_country[c] = indexed_by_country.get(c, 0) + len(ss)
    nodes = [
        _node(
            f"c:{c}",
            "country",
            _country_name(c),
            count=n,
            indexed=indexed_by_country.get(c, 0),
            has_children=True,
        )
        for c, n in counts.items()
        if n
    ]
    # 中国排第一，全球/国际第二，其余按门户数
    nodes.sort(key=lambda x: (x["id"] != "c:CN", x["id"] != f"c:{WORLD}", -x["count"], x["name"]))
    return nodes


def _country(ctx: _Ctx, country: str, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    groups = _group_counts(ctx, country)
    orphans = ctx.orphans.get(country, [])
    total = sum(sum(g.values()) for g in groups.values()) + len(orphans)
    if total <= FLAT:
        rows, _, _ = _portal_rows(ctx, country, None, None, FLAT, 0, None)
        nodes = [ctx.portal(r) for r in rows] + _orphan_nodes(ctx, country)
        return nodes, len(nodes)
    nodes = _tier_groups(ctx, country, "", groups.get("", {})) if groups.get("") else []
    if orphans:
        nodes.append(
            _node(
                f"o:{country}",
                "group",
                "目录来源（门户库里没有对应记录）",
                count=len(orphans),
                has_children=True,
            )
        )
    regs = [
        _node(
            f"r:{country}:{rg}",
            "region",
            _region_name(country, rg),
            count=sum(g.values()),
            has_children=True,
        )
        for rg, g in groups.items()
        if rg
    ]
    regs.sort(key=lambda x: (-x["count"], x["name"]))
    nodes += regs
    return nodes[offset : offset + limit], len(nodes)


def _region(
    ctx: _Ctx, country: str, region: str, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    counts = _group_counts(ctx, country).get(region, {})
    if sum(counts.values()) <= FLAT:
        rows, total, _ = _portal_rows(ctx, country, region, None, FLAT, 0, None)
        return [ctx.portal(r) for r in rows], total
    nodes = _tier_groups(ctx, country, region, counts)
    return nodes[offset : offset + limit], len(nodes)


def _catalog_children(
    ctx: _Ctx, node: str, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    out = tree_children(
        ctx.cat, SearchRequest(), axis="source", node=node, limit=limit, offset=offset
    )
    nodes = []
    for c in out["children"]:
        rid = (c.get("select") or {}).get("inspect")
        nodes.append(
            _node(
                f"k:{c['id']}",
                "resource" if c.get("leaf") else "container",
                c["name"],
                count=c["count"],
                resource_type=c.get("kind"),
                resource_id=rid,
                has_children=bool(c["has_children"]),
            )
        )
    return nodes, out["total_children"]


def children(
    node: str | None = None,
    *,
    limit: int = 100,
    offset: int = 0,
    indexed_only: bool = False,
    focus: str | None = None,
) -> dict[str, Any]:
    """某个节点的直接子节点（node 为空 = 顶层）。focus=门户 id 时翻到包含它的那一页（用于"在树中定位"）。"""
    limit, offset = max(1, min(limit, LIMIT_MAX)), max(0, offset)
    with guard(), open_catalog(require_data=False) as cat:
        conn = pd._open_portals()
        if conn is None:
            raise ServiceError(
                "门户库为空，请先运行 datafinder sync",
                code="INDEX_NOT_READY",
                next_action="datafinder sync",
            )
        try:
            ctx = _Ctx(cat, conn, indexed_only)
            kind, _, rest = (node or "").partition(":")
            if not node:
                nodes = _root(ctx)
                total = len(nodes)
                nodes = nodes[offset : offset + limit]
            elif kind == "c":
                nodes, total = _country(ctx, rest, limit, offset)
            elif kind == "o":
                nodes = _orphan_nodes(ctx, rest)
                total = len(nodes)
            elif kind == "r":
                country, _, region = rest.partition(":")
                nodes, total = _region(ctx, country, region, limit, offset)
            elif kind == "t":
                country, region, tier = rest.split(":", 2)
                rows, total, offset = _portal_rows(
                    ctx, country, region, int(tier), limit, offset, focus
                )
                nodes = [ctx.portal(r) for r in rows]
            elif kind == "p":
                e = ctx.d.entry(rest) if rest in ctx.d.portals else None
                if e is None:
                    raise ServiceError(f"门户库里没有 {rest!r}", code="NOT_FOUND")
                ids = e["integration"]["linked_source_ids"]
                nodes = [ctx.source_node(s) for s in ids if ctx.d.sources[s].resources]
                total = len(nodes)
            elif kind == "k":
                nodes, total = _catalog_children(ctx, rest, limit, offset)
            else:
                raise ServiceError(
                    f"未知节点 {node!r}",
                    code="NOT_FOUND",
                    next_action="从顶层（不带 node）逐级展开",
                )
        finally:
            conn.close()
    return {
        "node": node,
        "children": nodes,
        "total_children": total,
        "offset": offset,
        "limit": limit,
        "indexed_only": indexed_only,
    }


def search(q: str, *, limit: int = 20) -> dict[str, Any]:
    """按名称/描述找门户，并给出它在树里的位置（trail：依次展开这些节点就能到达，最后一步用 focus 翻页）。"""
    q = q.strip()
    if not q:
        raise ServiceError("q 不能为空", code="INVALID_ARGUMENT")
    with guard(), open_catalog(require_data=False) as cat:
        conn = pd._open_portals()
        if conn is None:
            raise ServiceError(
                "门户库为空，请先运行 datafinder sync",
                code="INDEX_NOT_READY",
                next_action="datafinder sync",
            )
        try:
            ctx = _Ctx(cat, conn, False)
            _, hits = search_portals(conn, q, limit=max(1, min(limit, 50)))
            groups: dict[str, dict[str, dict[int, int]]] = {}
            out = []
            for h in hits:
                row = h.row
                c, rg, tr = row["country"] or "", row["region"] or "", row["tier"] or 0
                g = groups.setdefault(c, _group_counts(ctx, c))
                n_country = sum(sum(x.values()) for x in g.values()) + len(ctx.orphans.get(c, []))
                n_region = sum(g.get(rg, {}).values())
                trail = [f"c:{c}"]
                if n_country > FLAT:
                    if rg and n_region > FLAT:
                        trail += [f"r:{c}:{rg}", f"t:{c}:{rg}:{tr}"]
                    elif rg:
                        trail += [f"r:{c}:{rg}"]
                    else:
                        trail += [f"t:{c}::{tr}"]
                crumbs = (
                    [_country_name(c)]
                    + ([_region_name(c, rg)] if rg else [])
                    + ([_tier_name(tr)] if n_country > FLAT and (not rg or n_region > FLAT) else [])
                )
                out.append({"node": ctx.portal(row), "trail": trail, "path": " › ".join(crumbs)})
        finally:
            conn.close()
    return {"query": q, "results": out}
