"""门户树：全部数据门户按 国家 → 地区 → 机构类型 → 门户 逐层展开；已接入的门户还能继续展开到它的来源与资源。

和"目录树"（资源按主题/地域/来源）互补：这棵树以**门户（网站）**为主体，门户库里的 4 万多个门户全部在里面，
其中与目录来源关联上的（见 portal_directory）可以一路点开到具体资源。节点按需加载，一次只返回一层。

三个接口配合成"左树 · 中列表 · 右详情"：
- children  左边树：某节点的直接子节点
- listing   中间：某节点之下的全部门户（拍平、分页、排序）
- summary   右边：某节点的概况（位置、数量、按数据形态/机构类型的分布；门户节点给出门户本身）
筛选（数据形态、只看官方、有 API、只看已接入）对三者同时生效，树上的计数随筛选重算。

节点 id（不透明，前端原样回传）：
  c:<国家>            国家/地区          r:<国家>:<地区>        国家下的地区（省、州…）
  t:<国家>:<地区>:<档>  机构类型分组        p:<门户 id>           门户（有关联来源时可展开）
  o:<国家>            目录里有、门户库里没有对应记录的来源（只在国家下出现）
  s:k=v;k=v          换一条轴的分组：kind=<数据形态>、tier=<档>、country=<国家>（按 数据形态/机构类型 → 国家 → 门户）
  k:<目录节点 id>      目录里的来源/资源（沿用目录树的节点 id）
小分组（门户数 ≤ FLAT）不再拆机构类型，直接列门户，避免"点开只有一两项"的空层级。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from datafinder.catalog import SearchRequest, regions
from datafinder.catalog.repository import Catalog
from datafinder.catalog.tree import _source_path, tree_children
from datafinder.portals.browse import _ORDER, country_zh_name, portal_dict
from datafinder.portals.facets import load_facets
from datafinder.portals.search import search_portals

from . import portal_directory as pd
from .common import open_catalog
from .errors import ServiceError, guard

FLAT = 15  # 一个分组的门户数不超过它就直接列出
LIMIT_MAX = 300
WORLD = "World"
AXES = ("geo", "kind", "tier")
SORTS = {
    "official": _ORDER["official"],
    "name": "COALESCE(p.name_zh, p.name) COLLATE NOCASE",
}


@dataclass(frozen=True)
class Filters:
    """四个筛选：数据形态、只看官方（机构类型 1–3 档）、有 API、只看已接入。"""

    kind: str | None = None
    official: bool = False
    api: bool = False
    indexed: bool = False

    @property
    def any_portal_attr(self) -> bool:
        """门户库里没有记录的来源没有这些属性，带这些筛选时不出现。"""
        return bool(self.kind or self.official or self.api)


def _tier_name(tier: int) -> str:
    for t in load_facets().tiers:
        if t.tier == tier:
            return t.zh
    return "未分类"


def _kind_name(key: str) -> str:
    for k in load_facets().kinds:
        if k.key == key:
            return k.zh
    return key or "未分类"


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


# ───────────── 范围（scope）：节点对应的一组门户 ─────────────


def _scope(node: str | None) -> dict[str, Any] | None:
    """c/r/t/s 节点（以及根）对应的筛选范围；p/k/o 节点不是"一组门户"，返回 None。"""
    if not node:
        return {}
    kind, _, rest = node.partition(":")
    try:
        if kind == "c":
            return {"country": rest}
        if kind == "r":
            country, _, region = rest.partition(":")
            return {"country": country, "region": region}
        if kind == "t":
            country, region, tier = rest.split(":", 2)
            return {"country": country, "region": region, "tier": int(tier)}
        if kind == "s":
            out: dict[str, Any] = {}
            for part in rest.split(";"):
                k, _, v = part.partition("=")
                if k not in ("kind", "tier", "country"):
                    raise ValueError(k)
                out[k] = int(v) if k == "tier" else v
            return out
    except ValueError as exc:
        raise ServiceError(f"节点 id 不合法：{node!r}", code="NOT_FOUND") from exc
    return None


def _scope_sql(scope: dict[str, Any]) -> tuple[list[str], list]:
    parts, args = [], []
    if "country" in scope:
        parts.append("COALESCE(p.country,'') = ?")
        args.append(scope["country"])
    if "region" in scope:
        parts.append("COALESCE(p.region,'') = ?")
        args.append(scope["region"])
    if "tier" in scope:
        parts.append("COALESCE(p.tier,0) = ?")
        args.append(scope["tier"])
    if "kind" in scope:
        parts.append("p.kind = ?")
        args.append(scope["kind"])
    return parts, args


def _scope_id(scope: dict[str, Any]) -> str:
    return "s:" + ";".join(f"{k}={v}" for k, v in scope.items())


class _Ctx:
    """一次请求内共用的东西：目录关联、门户库连接、筛选条件。"""

    def __init__(self, cat: Catalog, conn: sqlite3.Connection, filters: Filters) -> None:
        self.cat, self.conn, self.f = cat, conn, filters
        self.d = pd.cached_directory(cat, conn)
        self.indexed_ids = {
            pid
            for pid in self.d.portals
            if self.d.entry(pid)["integration"]["level"] != "directory_only"
        }
        # 目录里有、门户库里没有记录的来源，按它覆盖的地域归到国家
        self.orphans: dict[str, list[Any]] = {}
        if not filters.any_portal_attr:
            for s in self.d.source_only:
                if s.resources:
                    country = next(
                        (c for rid in s.region_ids if (c := _country_of_region(rid))), ""
                    )
                    self.orphans.setdefault(country, []).append(s)

    def where(
        self,
        scope: dict[str, Any] | None = None,
        *,
        skip: tuple[str, ...] = (),
        extra: str = "",
        args: list | None = None,
    ) -> tuple[str, list]:
        """范围 + 筛选的 SQL 条件。skip=("kind",) 用于算"数据形态"自己的分布（兄弟项才能保持可见）。"""
        parts, a = _scope_sql(scope or {})
        if scope is None:
            parts, a = [], []
        if extra:
            parts.append(extra)
            a += list(args or [])
        f = self.f
        if f.kind and "kind" not in skip:
            parts.append("p.kind = ?")
            a.append(f.kind)
        if f.official and "official" not in skip:
            parts.append("p.tier IN (1, 2, 3)")
        if f.api and "api" not in skip:
            parts.append("p.has_api = 1")
        if f.indexed and "indexed" not in skip:
            ids = sorted(self.indexed_ids)
            parts.append(f"p.id IN ({','.join('?' * len(ids))})" if ids else "0")
            a += ids
        return (" AND ".join(parts) or "1=1"), a

    def orphan_list(self, scope: dict[str, Any]) -> list[Any]:
        """某范围内的"无门户记录的来源"：只在按国家（或全部）看、且没有门户属性筛选时出现。"""
        if "kind" in scope or "tier" in scope or "region" in scope:
            return []
        if "country" in scope:
            return list(self.orphans.get(scope["country"], []))
        return [s for ss in self.orphans.values() for s in ss]

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
            country_code=row["country"] or "",
            country=info["country"],
            region=info["region"],
            official=info["official"],
            org_type=info["org_type"],
            data_kind=info["kind"],
            type=info["type"],
            api=info["api"],
            openness=info["openness"],
            verify=info["verify"],
            note=info["note"],
            description=info["description"],
            industries=info["industries"],
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
    scope: dict[str, Any],
    limit: int,
    offset: int,
    focus: str | None = None,
    *,
    sort: str = "official",
    q: str = "",
) -> tuple[list[sqlite3.Row], int, int]:
    extra, eargs = "", []
    if q:
        extra = "(p.name LIKE ? OR p.name_zh LIKE ? OR p.url LIKE ? OR p.desc_zh LIKE ?)"
        eargs = [f"%{q}%"] * 4
    where, a = ctx.where(scope, extra=extra, args=eargs)
    order = SORTS.get(sort, SORTS["official"])
    total = ctx.conn.execute(f"SELECT COUNT(*) FROM portals p WHERE {where}", a).fetchone()[0]
    if focus:  # 定位：翻到包含该门户的那一页
        ids = [
            r[0]
            for r in ctx.conn.execute(
                f"SELECT p.id FROM portals p WHERE {where} ORDER BY {order}", a
            )
        ]
        if focus in ids:
            offset = (ids.index(focus) // limit) * limit
    rows = ctx.conn.execute(
        f"SELECT p.* FROM portals p WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*a, limit, offset],
    ).fetchall()
    return rows, total, offset


def _orphan_nodes(ctx: _Ctx, country: str) -> list[dict[str, Any]]:
    return [ctx.source_node(s.source_id) for s in ctx.orphans.get(country, [])]


def _tier_groups(country: str, region: str, counts: dict[int, int]) -> list[dict[str, Any]]:
    return [
        _node(f"t:{country}:{region}:{tr}", "tier", _tier_name(tr), count=n, has_children=True)
        for tr, n in sorted(counts.items(), key=lambda x: (x[0] == 0, x[0]))
    ]


def _group_counts(ctx: _Ctx, country: str) -> dict[str, dict[int, int]]:
    where, a = ctx.where({"country": country})
    out: dict[str, dict[int, int]] = {}
    for r in ctx.conn.execute(
        f"SELECT COALESCE(p.region,'') rg, COALESCE(p.tier,0) tr, COUNT(*) n FROM portals p WHERE {where} GROUP BY 1, 2",
        a,
    ):
        out.setdefault(r["rg"], {})[r["tr"]] = r["n"]
    return out


def _indexed_by_country(ctx: _Ctx) -> dict[str, int]:
    out: dict[str, int] = {}
    if ctx.indexed_ids:
        marks = ",".join("?" * len(ctx.indexed_ids))
        for r in ctx.conn.execute(
            f"SELECT COALESCE(country,'') c, COUNT(*) n FROM portals WHERE id IN ({marks}) GROUP BY 1",
            sorted(ctx.indexed_ids),
        ):
            out[r["c"]] = r["n"]
    for c, ss in ctx.orphans.items():
        out[c] = out.get(c, 0) + len(ss)
    return out


def _root(ctx: _Ctx, axis: str = "geo") -> list[dict[str, Any]]:
    if axis == "kind":
        where, a = ctx.where({})
        rows = ctx.conn.execute(
            f"SELECT p.kind k, COUNT(*) n FROM portals p WHERE {where} GROUP BY 1", a
        ).fetchall()
        order = {k.key: i for i, k in enumerate(load_facets().kinds)}
        nodes = [
            _node(
                _scope_id({"kind": r["k"]}),
                "group",
                _kind_name(r["k"]),
                count=r["n"],
                has_children=True,
            )
            for r in rows
        ]
        return sorted(nodes, key=lambda x: order.get(x["id"].split("=", 1)[1], 99))
    if axis == "tier":
        where, a = ctx.where({})
        rows = ctx.conn.execute(
            f"SELECT COALESCE(p.tier,0) t, COUNT(*) n FROM portals p WHERE {where} GROUP BY 1", a
        ).fetchall()
        nodes = [
            _node(
                _scope_id({"tier": r["t"]}),
                "group",
                _tier_name(r["t"]),
                count=r["n"],
                has_children=True,
            )
            for r in rows
        ]
        return sorted(nodes, key=lambda x: (x["id"].endswith("=0"), x["id"]))
    where, a = ctx.where({})
    counts = {
        r["c"]: r["n"]
        for r in ctx.conn.execute(
            f"SELECT COALESCE(p.country,'') c, COUNT(*) n FROM portals p WHERE {where} GROUP BY 1",
            a,
        )
    }
    indexed = _indexed_by_country(ctx)
    for c, ss in ctx.orphans.items():
        counts[c] = counts.get(c, 0) + len(ss)
    nodes = [
        _node(
            f"c:{c}",
            "country",
            _country_name(c),
            count=n,
            indexed=indexed.get(c, 0),
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
        rows, _, _ = _portal_rows(ctx, {"country": country}, FLAT, 0)
        nodes = [ctx.portal(r) for r in rows] + _orphan_nodes(ctx, country)
        return nodes, len(nodes)
    nodes = _tier_groups(country, "", groups.get("", {})) if groups.get("") else []
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
        rows, total, _ = _portal_rows(ctx, {"country": country, "region": region}, FLAT, 0)
        return [ctx.portal(r) for r in rows], total
    nodes = _tier_groups(country, region, counts)
    return nodes[offset : offset + limit], len(nodes)


def _scoped(
    ctx: _Ctx, scope: dict[str, Any], limit: int, offset: int, focus: str | None
) -> tuple[list[dict[str, Any]], int, int]:
    """换轴后的分组：先按国家分，选定国家后列门户（分页）。"""
    if "country" in scope:
        rows, total, offset = _portal_rows(ctx, scope, limit, offset, focus)
        return [ctx.portal(r) for r in rows], total, offset
    where, a = ctx.where(scope)
    nodes = [
        _node(
            _scope_id({**scope, "country": r["c"]}),
            "country",
            _country_name(r["c"]),
            count=r["n"],
            has_children=True,
        )
        for r in ctx.conn.execute(
            f"SELECT COALESCE(p.country,'') c, COUNT(*) n FROM portals p WHERE {where} GROUP BY 1",
            a,
        )
    ]
    nodes.sort(key=lambda x: (not x["id"].endswith("country=CN"), -x["count"], x["name"]))
    return nodes[offset : offset + limit], len(nodes), offset


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


def _open():
    return open_catalog(require_data=False)


def _need_portals() -> sqlite3.Connection:
    conn = pd._open_portals()
    if conn is None:
        raise ServiceError(
            "门户库为空，请先运行 datafinder sync",
            code="INDEX_NOT_READY",
            next_action="datafinder sync",
        )
    return conn


def _filters(indexed_only: bool, kind: str | None, official: bool, api: bool) -> Filters:
    if kind and not any(k.key == kind for k in load_facets().kinds):
        raise ServiceError(
            f"未知数据形态 {kind!r}",
            code="INVALID_FACET_ID",
            next_action="可选：" + "、".join(k.key for k in load_facets().kinds),
        )
    return Filters(kind=kind or None, official=official, api=api, indexed=indexed_only)


# ───────────── 对外三个接口 ─────────────


def children(
    node: str | None = None,
    *,
    limit: int = 100,
    offset: int = 0,
    indexed_only: bool = False,
    focus: str | None = None,
    kind: str | None = None,
    official: bool = False,
    api: bool = False,
    axis: str = "geo",
) -> dict[str, Any]:
    """左边树：某个节点的直接子节点（node 为空 = 顶层，axis 决定顶层按 国家/数据形态/机构类型 分）。"""
    if axis not in AXES:
        raise ServiceError(f"axis 只能是 {' / '.join(AXES)}", code="INVALID_ARGUMENT")
    limit, offset = max(1, min(limit, LIMIT_MAX)), max(0, offset)
    flt = _filters(indexed_only, kind, official, api)
    with guard(), _open() as cat:
        conn = _need_portals()
        try:
            ctx = _Ctx(cat, conn, flt)
            k, _, rest = (node or "").partition(":")
            if not node:
                nodes = _root(ctx, axis)
                total = len(nodes)
                nodes = nodes[offset : offset + limit]
            elif k == "c":
                nodes, total = _country(ctx, rest, limit, offset)
            elif k == "o":
                nodes = _orphan_nodes(ctx, rest)
                total = len(nodes)
            elif k == "r":
                country, _, region = rest.partition(":")
                nodes, total = _region(ctx, country, region, limit, offset)
            elif k == "t":
                scope = _scope(node) or {}
                rows, total, offset = _portal_rows(ctx, scope, limit, offset, focus)
                nodes = [ctx.portal(r) for r in rows]
            elif k == "s":
                nodes, total, offset = _scoped(ctx, _scope(node) or {}, limit, offset, focus)
            elif k == "p":
                e = ctx.d.entry(rest) if rest in ctx.d.portals else None
                if e is None:
                    raise ServiceError(f"门户库里没有 {rest!r}", code="NOT_FOUND")
                ids = e["integration"]["linked_source_ids"]
                nodes = [ctx.source_node(s) for s in ids if ctx.d.sources[s].resources]
                total = len(nodes)
            elif k == "k":
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


def _path(ctx: _Ctx, node: str | None) -> list[dict[str, str]]:
    """节点在树里的位置（面包屑）。"""
    if not node:
        return []
    k, _, rest = node.partition(":")
    if k == "c":
        return [{"id": node, "name": _country_name(rest)}]
    if k == "r":
        country, _, region = rest.partition(":")
        return [
            {"id": f"c:{country}", "name": _country_name(country)},
            {"id": node, "name": _region_name(country, region)},
        ]
    if k == "t":
        country, region, tier = rest.split(":", 2)
        out = [{"id": f"c:{country}", "name": _country_name(country)}]
        if region:
            out.append({"id": f"r:{country}:{region}", "name": _region_name(country, region)})
        return [*out, {"id": node, "name": _tier_name(int(tier))}]
    if k == "o":
        return [
            {"id": f"c:{rest}", "name": _country_name(rest)},
            {"id": node, "name": "目录来源（门户库里没有对应记录）"},
        ]
    if k == "s":
        scope = _scope(node) or {}
        out, acc = [], {}
        for key in ("kind", "tier", "country"):
            if key in scope:
                acc[key] = scope[key]
                name = (
                    _kind_name(scope[key])
                    if key == "kind"
                    else _tier_name(scope[key])
                    if key == "tier"
                    else _country_name(scope[key])
                )
                out.append({"id": _scope_id(dict(acc)), "name": name})
        return out
    if k == "p":
        row = ctx.conn.execute("SELECT * FROM portals WHERE id = ?", (rest,)).fetchone()
        if row is None:
            raise ServiceError(f"门户库里没有 {rest!r}", code="NOT_FOUND")
        ids, names = _trail(ctx, row, {})
        return [
            *({"id": i, "name": n} for i, n in zip(ids, names, strict=True)),
            {"id": node, "name": portal_dict(row)["name"]},
        ]
    if k == "k":
        return [
            {"id": f"k:{p['id']}", "name": p["name"]}
            for p in _source_path(ctx.cat, rest)
            if not p["id"].startswith("domain:")
        ]
    raise ServiceError(f"未知节点 {node!r}", code="NOT_FOUND")


def listing(
    node: str | None = None,
    *,
    limit: int = 30,
    offset: int = 0,
    sort: str = "official",
    q: str = "",
    indexed_only: bool = False,
    kind: str | None = None,
    official: bool = False,
    api: bool = False,
) -> dict[str, Any]:
    """中间列表：某个国家/地区/机构类型/数据形态之下的全部门户（拍平、分页、排序）。"""
    limit, offset = max(1, min(limit, 100)), max(0, offset)
    if sort not in SORTS:
        raise ServiceError(f"sort 只能是 {' / '.join(SORTS)}", code="INVALID_ARGUMENT")
    scope = _scope(node)
    if scope is None:
        raise ServiceError(
            f"{node!r} 不是一组门户",
            code="INVALID_ARGUMENT",
            next_action="用 children 看它的子节点",
        )
    flt = _filters(indexed_only, kind, official, api)
    with guard(), _open() as cat:
        conn = _need_portals()
        try:
            ctx = _Ctx(cat, conn, flt)
            q = q.strip()
            rows, n_portals, _ = _portal_rows(ctx, scope, limit, offset, sort=sort, q=q)
            items = [ctx.portal(r) for r in rows]
            orphans = [] if q else ctx.orphan_list(scope)
            if orphans:  # 门户库里没有记录的来源接在最后，计数与树一致
                room = limit - len(items)
                start = max(0, offset - n_portals)
                if room > 0:
                    items += [ctx.source_node(s.source_id) for s in orphans[start : start + room]]
            total = n_portals + len(orphans)
            path = _path(ctx, node)
        finally:
            conn.close()
    return {
        "node": node,
        "path": path,
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "sort": sort,
    }


def summary(
    node: str | None = None,
    *,
    indexed_only: bool = False,
    kind: str | None = None,
    official: bool = False,
    api: bool = False,
) -> dict[str, Any]:
    """右边概况：节点位置；一组门户给出数量与按数据形态/机构类型的分布；门户/来源/资源给出它本身。"""
    flt = _filters(indexed_only, kind, official, api)
    with guard(), _open() as cat:
        conn = _need_portals()
        try:
            ctx = _Ctx(cat, conn, flt)
            path = _path(ctx, node)
            scope = _scope(node)
            if scope is None:
                k, _, rest = node.partition(":")
                out: dict[str, Any] = {"node": node, "kind": k, "path": path}
                if k == "p":
                    row = conn.execute("SELECT * FROM portals WHERE id = ?", (rest,)).fetchone()
                    out["portal"] = ctx.portal(row)
                return out
            where_all, a_all = ctx.where(scope)
            total = conn.execute(
                f"SELECT COUNT(*) FROM portals p WHERE {where_all}", a_all
            ).fetchone()[0]
            where_k, a_k = ctx.where(
                scope, skip=("kind",)
            )  # 数据形态自己的分布不受数据形态筛选影响
            kinds = {
                r["k"]: r["n"]
                for r in conn.execute(
                    f"SELECT p.kind k, COUNT(*) n FROM portals p WHERE {where_k} GROUP BY 1", a_k
                )
            }
            tiers = {
                r["t"]: r["n"]
                for r in conn.execute(
                    f"SELECT COALESCE(p.tier,0) t, COUNT(*) n FROM portals p WHERE {where_all} GROUP BY 1",
                    a_all,
                )
            }

            ids = sorted(ctx.indexed_ids)
            marks = ",".join("?" * len(ids))
            indexed = _count(ctx, scope, f"p.id IN ({marks})", ids, ("indexed",)) if ids else 0
            orphans = ctx.orphan_list(scope)
            order = {k.key: i for i, k in enumerate(load_facets().kinds)}
            return {
                "node": node,
                "kind": "scope",
                "name": path[-1]["name"] if path else "全部门户",
                "path": path,
                "total": total + len(orphans),
                "portals": total,
                "kinds": sorted(
                    ({"key": k, "name": _kind_name(k), "count": n} for k, n in kinds.items()),
                    key=lambda x: order.get(x["key"], 99),
                ),
                "tiers": sorted(
                    ({"tier": t, "name": _tier_name(t), "count": n} for t, n in tiers.items()),
                    key=lambda x: (x["tier"] == 0, x["tier"]),
                ),
                "official": _count(ctx, scope, "p.tier IN (1, 2, 3)", [], ("official",)),
                "api": _count(ctx, scope, "p.has_api = 1", [], ("api",)),
                "indexed": indexed + len(orphans),
            }
        finally:
            conn.close()


def _count(ctx: _Ctx, scope: dict[str, Any], clause: str, args: list, skip: tuple[str, ...]) -> int:
    """范围内满足某条件的门户数（其余筛选照常生效，该条件自己的筛选不算，这样开关的计数才有意义）。"""
    w, a = ctx.where(scope, skip=skip, extra=clause, args=args)
    return ctx.conn.execute(f"SELECT COUNT(*) FROM portals p WHERE {w}", a).fetchone()[0]


def _trail(
    ctx: _Ctx, row: sqlite3.Row, cache: dict[str, dict[str, dict[int, int]]]
) -> tuple[list[str], list[str]]:
    """门户在"按地域"树里的位置：依次展开这些节点即可到达（返回节点 id 与对应的名称）。"""
    c, rg, tr = row["country"] or "", row["region"] or "", row["tier"] or 0
    g = cache.setdefault(c, _group_counts(ctx, c))
    n_country = sum(sum(x.values()) for x in g.values()) + len(ctx.orphans.get(c, []))
    n_region = sum(g.get(rg, {}).values())
    ids, names = [f"c:{c}"], [_country_name(c)]
    if n_country > FLAT:
        if rg:
            ids.append(f"r:{c}:{rg}")
            names.append(_region_name(c, rg))
        if not rg or n_region > FLAT:
            ids.append(f"t:{c}:{rg}:{tr}")
            names.append(_tier_name(tr))
    return ids, names


def search(q: str, *, limit: int = 20) -> dict[str, Any]:
    """按名称/描述找门户，并给出它在树里的位置（trail：依次展开这些节点就能到达，最后一步用 focus 翻页）。"""
    q = q.strip()
    if not q:
        raise ServiceError("q 不能为空", code="INVALID_ARGUMENT")
    with guard(), _open() as cat:
        conn = _need_portals()
        try:
            ctx = _Ctx(cat, conn, Filters())
            _, hits = search_portals(conn, q, limit=max(1, min(limit, 50)))
            cache: dict[str, dict[str, dict[int, int]]] = {}
            out = []
            for h in hits:
                ids, names = _trail(ctx, h.row, cache)
                out.append({"node": ctx.portal(h.row), "trail": ids, "path": " › ".join(names)})
        finally:
            conn.close()
    return {"query": q, "results": out}
