"""目录树：同一批资源按三条轴（主题、地域、来源）展示的"投影"。

树不是新的数据，只是对检索结果的另一种看法：
- 计数 = 当前请求（关键词 + 其他筛选）的完整命中集合里，落在该节点下的**不同资源数**（与 search 的 total 同一口径，
  不基于第一页或候选上限）。本轴自己的筛选不参与计数（兄弟节点才能保持可见，与分面计数一致）。
- 无状态：视图 = 规范化后的 SearchRequest + 轴 + 节点；一次只返回一个节点的直接子节点（按需展开，计数为 0 的不返回）。
- 主题轴：taxonomy 的两级主题 + "未归类"；地域轴：国家 → 省 → 地级 → 县（覆盖口径，不可相加）；
  来源轴：领域 → 来源 → 来源内真实层级（库/分类/数据集/指标…）。

计数方式（都是一次或常数次 SQL，不随节点数增长）：
- 主题：VALUES 映射 + GROUP BY；
- 地域：兄弟节点共享"上级含下级资源"集合，只需一次基数 + 一次 GROUP BY；
- 来源：按父节点分组 → 在内存里沿父链汇总（不需要物化路径列）。
"""

from __future__ import annotations

from typing import Any

from .. import domains
from . import regions, taxonomy
from .kinds import container_kinds
from .query import SearchRequest, Where
from .repository import Catalog
from .search import prepare

AXES = {
    "topic": "按主题",
    "region": "按地域",
    "source": "按来源",
}
UNCLASSIFIED = "_none"
UNCLASSIFIED_NAME = "未归类"
MAX_LIMIT = 200
_BATCH = 400

_DOMAIN_NAMES: dict[str, str] = {}


class InvalidNode(ValueError):
    """节点 id 不属于该轴，或已不存在。"""


def register_domain(domain_id: str, name_zh: str) -> None:
    """领域包登记自己在来源轴上的显示名。"""
    _DOMAIN_NAMES[domain_id] = name_zh


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _node(
    id_: str,
    name: str,
    count: int,
    *,
    has_children: bool,
    select: dict[str, str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """树节点。select = 选中该节点时应附加的检索参数（与 /api/catalog/search 的参数同名）。"""
    return {
        "id": id_,
        "name": name,
        "count": count,
        "has_children": has_children,
        "select": select,
        **extra,
    }


def _sorted(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(nodes, key=lambda n: (n["id"] == UNCLASSIFIED, -n["count"], n["name"]))


# ───────────── 主题轴 ─────────────


def _topic_children(cat: Catalog, where: Where, node: str | None) -> tuple[list[dict], list[dict]]:
    if node is not None and taxonomy.get(node) is None:
        raise InvalidNode(f"未知主题节点 {node!r}")
    kids = [t for t in taxonomy.topics().values() if t.parent_id == node and not t.reserved]
    if not kids:
        return [], _topic_path(node)
    mapping = [(m, k.id) for k in kids for m in (k.id, *taxonomy.descendants(k.id))]
    clause, args = where.sql(skip=("topic",))
    values = ",".join("(?,?)" for _ in mapping)
    rows = cat.conn.execute(
        f"WITH m(tid, node) AS (VALUES {values})"
        " SELECT m.node AS id, COUNT(DISTINCT r.resource_id) AS n FROM resources r"
        " JOIN resource_topics rt ON rt.resource_id = r.resource_id JOIN m ON m.tid = rt.topic_id"
        f" WHERE {clause} GROUP BY m.node",
        [x for pair in mapping for x in pair] + args,
    ).fetchall()
    counts = {r["id"]: r["n"] for r in rows}
    nodes = [
        _node(
            k.id,
            k.name_zh,
            counts[k.id],
            has_children=any(not c.reserved for c in taxonomy.children(k.id)),
            select={"topic": k.id},
        )
        for k in kids
        if counts.get(k.id)
    ]
    if node is None:
        none = cat.conn.execute(
            f"SELECT COUNT(*) FROM resources r WHERE {clause}"
            " AND NOT EXISTS (SELECT 1 FROM resource_topics x WHERE x.resource_id = r.resource_id)",
            args,
        ).fetchone()[0]
        if none:
            nodes.append(
                _node(UNCLASSIFIED, UNCLASSIFIED_NAME, none, has_children=False, selectable=False)
            )
    return _sorted(nodes), _topic_path(node)


def _topic_path(node: str | None) -> list[dict]:
    if node is None:
        return []
    chain = [*reversed(taxonomy.ancestors(node)), node]
    return [{"id": t, "name": taxonomy.get(t).name_zh} for t in chain if taxonomy.get(t)]


# ───────────── 地域轴 ─────────────


def _region_index() -> dict[str | None, list[regions.Region]]:
    idx: dict[str | None, list[regions.Region]] = {}
    for r in regions.regions().values():
        idx.setdefault(r.parent_id, []).append(r)
    return idx


def _fine_levels(level: str) -> tuple[str, ...] | None:
    """含下级资源要覆盖该级别，报告粒度必须够细（与检索的地域条件同一规则）。"""
    return {"prefecture": ("prefecture", "county"), "county": ("county",)}.get(level)


def _region_children(
    cat: Catalog, where: Where, node: str | None
) -> tuple[list[dict], list[dict], int]:
    index = _region_index()
    if node is None:  # 顶层：各国 + "全球/国际"整体
        kids = [r for r in regions.regions().values() if r.level in ("country", "world")]
    else:
        if regions.get(node) is None:
            raise InvalidNode(f"未知地域节点 {node!r}")
        kids = list(index.get(node, []))
    clause, args = where.sql(skip=("region",))
    unannotated = cat.conn.execute(
        f"SELECT COUNT(*) FROM resources r WHERE {clause}"
        " AND NOT EXISTS (SELECT 1 FROM resource_regions x WHERE x.resource_id = r.resource_id)",
        args,
    ).fetchone()[0]
    if not kids:
        return [], _region_path(node), unannotated

    rowset = {r[0] for r in cat.conn.execute("SELECT DISTINCT region_id FROM resource_regions")}
    fine_scope = {
        r[0]
        for r in cat.conn.execute(
            "SELECT DISTINCT region_id FROM resource_regions"
            " WHERE scope_mode = 'descendants' AND granularity IN ('prefecture', 'county')"
        )
    }
    groups: dict[tuple, list[regions.Region]] = {}
    for k in kids:  # 兄弟节点的"上级"与粒度门槛相同，共用一次基数查询
        groups.setdefault((tuple(regions.ancestors(k.id)), _fine_levels(k.level)), []).append(k)
    counts: dict[str, int] = {}
    for (anc, fine), group in groups.items():
        a_sql, a_args = "", []
        if anc:
            gran = f" AND a.granularity IN ({_marks(len(fine))})" if fine else ""
            a_sql = (
                "EXISTS (SELECT 1 FROM resource_regions a WHERE a.resource_id = r.resource_id"
                f" AND a.scope_mode = 'descendants' AND a.region_id IN ({_marks(len(anc))}){gran})"
            )
            a_args = [*anc, *(fine or ())]
        base = (
            cat.conn.execute(
                f"SELECT COUNT(*) FROM resources r WHERE {clause} AND {a_sql}", [*args, *a_args]
            ).fetchone()[0]
            if a_sql
            else 0
        )
        not_a = f" AND NOT {a_sql}" if a_sql else ""
        ids = [g.id for g in group]
        for r in cat.conn.execute(
            "SELECT rr.region_id AS id, COUNT(DISTINCT r.resource_id) AS n FROM resources r"
            " JOIN resource_regions rr ON rr.resource_id = r.resource_id"
            f" WHERE {clause} AND rr.region_id IN ({_marks(len(ids))}){not_a} GROUP BY rr.region_id",
            [*args, *ids, *a_args],
        ):
            counts[r["id"]] = r["n"]
        for g in group:
            counts[g.id] = counts.get(g.id, 0) + base

    def deeper(r: regions.Region) -> bool:
        if r.level == "world" or not index.get(r.id):
            return False
        below = _descendants(index, r.id)
        return any(d in rowset for d in below) or any(
            a in fine_scope for a in (r.id, *regions.ancestors(r.id))
        )

    nodes = [
        _node(
            k.id,
            k.name_zh,
            counts[k.id],
            has_children=deeper(k),
            select={"region": k.id},
            level=k.level,
        )
        for k in kids
        if counts.get(k.id)
    ]
    return _sorted(nodes), _region_path(node), unannotated


def _descendants(index: dict, region_id: str) -> list[str]:
    out: list[str] = []
    stack = [region_id]
    while stack:
        for r in index.get(stack.pop(), []):
            out.append(r.id)
            stack.append(r.id)
    return out


def _region_path(node: str | None) -> list[dict]:
    if node is None:
        return []
    chain = [x for x in (*reversed(regions.ancestors(node)), node) if x != regions.WORLD]
    return [{"id": r, "name": regions.name_zh(r)} for r in chain]


# ───────────── 来源轴 ─────────────


def _source_children(
    cat: Catalog, where: Where, node: str | None, limit: int, offset: int
) -> tuple[list[dict], list[dict], int]:
    clause, args = where.sql(skip=("source", "domain"))
    if node is None:
        rows = cat.conn.execute(
            "SELECT s.domain_id AS id, COUNT(*) AS n FROM resources r"
            f" JOIN sources s ON s.source_id = r.source_id WHERE {clause} GROUP BY s.domain_id",
            args,
        ).fetchall()
        nodes = [
            _node(
                f"domain:{r['id']}",
                _DOMAIN_NAMES.get(r["id"], r["id"]),
                r["n"],
                has_children=True,
                select={"domain": r["id"]},
            )
            for r in rows
        ]
        return _page(_sorted(nodes), limit, offset), [], len(nodes)
    kind, _, key = node.partition(":")
    if kind == "domain":
        rows = cat.conn.execute(
            "SELECT s.source_id AS id, s.name AS name, COUNT(*) AS n FROM resources r"
            " JOIN sources s ON s.source_id = r.source_id"
            f" WHERE {clause} AND s.domain_id = ? GROUP BY s.source_id",
            [*args, key],
        ).fetchall()
        nodes = [
            _node(
                f"source:{r['id']}",
                r["name"],
                r["n"],
                has_children=True,
                select={"source": r["id"]},
            )
            for r in rows
        ]
        return _page(_sorted(nodes), limit, offset), _source_path(cat, node), len(nodes)
    if kind not in ("source", "res"):
        raise InvalidNode(f"来源轴的节点 id 应以 domain: / source: / res: 开头，收到 {node!r}")
    return _resource_children(cat, clause, args, kind, key, limit, offset, node)


def _page(nodes: list[dict], limit: int, offset: int) -> list[dict]:
    return nodes[offset : offset + limit]


def _resource_children(
    cat: Catalog,
    clause: str,
    args: list,
    kind: str,
    key: str,
    limit: int,
    offset: int,
    node: str,
) -> tuple[list[dict], list[dict], int]:
    if kind == "source":
        source_id, parent = key, None
        src = cat.conn.execute("SELECT 1 FROM sources WHERE source_id = ?", (key,)).fetchone()
        if not src:
            raise InvalidNode(f"目录里没有来源 {key!r}")
    else:
        row = cat.conn.execute(
            "SELECT source_id FROM resources WHERE resource_id = ?", (key,)
        ).fetchone()
        if not row:
            raise InvalidNode(f"目录里没有资源 {key!r}")
        source_id, parent = row["source_id"], key
    containers = sorted(container_kinds())
    cmarks = _marks(len(containers))
    scoped, scoped_args = f"{clause} AND r.source_id = ?", [*args, source_id]
    # 1) 叶子类命中：按父节点分组；2) 容器类命中：自身也算命中（数据集本身可被检索到）
    leaf_groups = {
        r["p"]: r["n"]
        for r in cat.conn.execute(
            f"SELECT r.parent_id AS p, COUNT(*) AS n FROM resources r WHERE {scoped}"
            f" AND r.kind NOT IN ({cmarks}) GROUP BY r.parent_id",
            [*scoped_args, *containers],
        )
    }
    matched_containers = [
        r["resource_id"]
        for r in cat.conn.execute(
            f"SELECT r.resource_id FROM resources r WHERE {scoped} AND r.kind IN ({cmarks})",
            [*scoped_args, *containers],
        )
    ]
    parent_of = _parent_chain(cat, [*(p for p in leaf_groups if p), *matched_containers])
    sub: dict[str, int] = {}

    def add(chain: list[str], n: int) -> None:
        """chain：从命中资源（或其父）到来源根；记到 node 下一层的那个容器上。"""
        if parent is None:
            child = chain[-1] if chain else None  # 根资源就是来源的直接子节点
        elif parent in chain and chain.index(parent) > 0:
            child = chain[chain.index(parent) - 1]
        else:
            return
        if child:
            sub[child] = sub.get(child, 0) + n

    for p, n in leaf_groups.items():
        if p:
            add(_chain(parent_of, p), n)
    for c in matched_containers:
        add(_chain(parent_of, c), 1)
    # 直接挂在 node 下的叶子（分页取，保证大数据集也不会一次返回全部）
    containers_here = _sorted(_container_nodes(cat, sub))
    leaf_total = leaf_groups.get(parent, 0)
    total = len(containers_here) + leaf_total
    out = _page(containers_here, limit, offset)
    room = limit - len(out)
    if room > 0 and leaf_total:
        leaf_offset = max(0, offset - len(containers_here))
        rows = cat.conn.execute(
            f"SELECT r.resource_id, r.title, r.kind FROM resources r WHERE {scoped}"
            f" AND r.parent_id IS ? AND r.kind NOT IN ({cmarks}) ORDER BY r.title LIMIT ? OFFSET ?",
            [*scoped_args, parent, *containers, room, leaf_offset],
        ).fetchall()
        out += [
            _node(
                f"res:{r['resource_id']}",
                r["title"],
                1,
                has_children=False,
                select={"inspect": r["resource_id"]},
                kind=r["kind"],
                leaf=True,
            )
            for r in rows
        ]
    return out, _source_path(cat, node), total


def _parent_chain(cat: Catalog, ids: list[str]) -> dict[str, str | None]:
    """沿父链向上把用到的节点的 parent_id 取全（每层一批查询）。"""
    parent_of: dict[str, str | None] = {}
    frontier = list(dict.fromkeys(ids))
    while frontier:
        batch_rows: list[tuple[str, str | None]] = []
        for i in range(0, len(frontier), _BATCH):
            part = frontier[i : i + _BATCH]
            batch_rows += [
                (r[0], r[1])
                for r in cat.conn.execute(
                    f"SELECT resource_id, parent_id FROM resources WHERE resource_id IN ({_marks(len(part))})",
                    part,
                )
            ]
        frontier = []
        for rid, pid in batch_rows:
            parent_of[rid] = pid
            if pid and pid not in parent_of:
                frontier.append(pid)
        frontier = list(dict.fromkeys(frontier))
    return parent_of


def _chain(parent_of: dict[str, str | None], start: str) -> list[str]:
    out: list[str] = []
    cur: str | None = start
    while cur and cur not in out:
        out.append(cur)
        cur = parent_of.get(cur)
    return out


def _container_nodes(cat: Catalog, sub: dict[str, int]) -> list[dict]:
    if not sub:
        return []
    ids = list(sub)
    meta: dict[str, Any] = {}
    for i in range(0, len(ids), _BATCH):
        part = ids[i : i + _BATCH]
        meta.update(
            {
                r["resource_id"]: r
                for r in cat.conn.execute(
                    "SELECT resource_id, title, kind FROM resources"
                    f" WHERE resource_id IN ({_marks(len(part))})",
                    part,
                )
            }
        )
    return [
        _node(
            f"res:{rid}",
            meta[rid]["title"],
            n,
            has_children=True,
            select={"inspect": rid},
            kind=meta[rid]["kind"],
        )
        for rid, n in sub.items()
        if rid in meta
    ]


def _source_path(cat: Catalog, node: str | None) -> list[dict]:
    if node is None:
        return []
    kind, _, key = node.partition(":")
    out: list[dict] = []
    if kind == "domain":
        return [{"id": node, "name": _DOMAIN_NAMES.get(key, key)}]
    if kind == "source":
        row = cat.conn.execute(
            "SELECT s.name, s.domain_id FROM sources s WHERE s.source_id = ?", (key,)
        ).fetchone()
        if not row:
            return []
        return [
            {
                "id": f"domain:{row['domain_id']}",
                "name": _DOMAIN_NAMES.get(row["domain_id"], row["domain_id"]),
            },
            {"id": node, "name": row["name"]},
        ]
    rid: str | None = key
    seen: set[str] = set()
    source_id = None
    while rid and rid not in seen:
        seen.add(rid)
        row = cat.conn.execute(
            "SELECT resource_id, title, parent_id, source_id FROM resources WHERE resource_id = ?",
            (rid,),
        ).fetchone()
        if not row:
            break
        source_id = row["source_id"]
        out.append({"id": f"res:{row['resource_id']}", "name": row["title"]})
        rid = row["parent_id"]
    out.reverse()
    return [*(_source_path(cat, f"source:{source_id}") if source_id else []), *out]


# ───────────── 入口 ─────────────


def tree_children(
    cat: Catalog,
    request: SearchRequest | None = None,
    *,
    axis: str,
    node: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """某条轴上某个节点的直接子节点（及面包屑路径）。node=None 表示该轴的顶层。"""
    if axis not in AXES:
        raise InvalidNode(f"axis 只能是 {sorted(AXES)}，收到 {axis!r}")
    domains.load_all()  # 领域包在这里登记来源轴上的显示名
    limit, offset = max(1, min(limit, MAX_LIMIT)), max(0, offset)
    req, _units, where, hints = prepare(request or SearchRequest())
    base = {"axis": axis, "node": node, "limit": limit, "offset": offset, "hints": hints}
    if where is None:
        return {**base, "total": 0, "children": [], "total_children": 0, "path": []}
    clause, args = where.sql()
    total = cat.conn.execute(f"SELECT COUNT(*) FROM resources r WHERE {clause}", args).fetchone()[0]
    extra: dict[str, Any] = {}
    if axis == "topic":
        children, path = _topic_children(cat, where, node)
        total_children = len(children)
        children = _page(children, limit, offset)
    elif axis == "region":
        children, path, unannotated = _region_children(cat, where, node)
        total_children = len(children)
        children = _page(children, limit, offset)
        if unannotated:
            extra["unannotated"] = unannotated
            extra["unannotated_note"] = (
                "另有这些资源没有地域标注（discovery 模式仍会返回，strict 不返回）"
            )
    else:
        children, path, total_children = _source_children(cat, where, node, limit, offset)
    return {
        **base,
        "total": total,
        "path": path,
        "children": children,
        "total_children": total_children,
        "note": _NOTES[axis],
        **extra,
    }


_NOTES = {
    "topic": "一个资源可属于多个主题，同级计数相加可能大于总数。",
    "region": "计数是“覆盖该地区”的资源数（含覆盖本地区的上级资源，如全国分省库），上下级不能相加。",
    "source": "计数是该节点之下命中的资源数（含版次，不按版次归并）。",
}


def axes_overview(cat: Catalog, request: SearchRequest | None = None) -> dict[str, Any]:
    """三条轴及当前请求下的总命中数（入口）。"""
    req, _units, where, hints = prepare(request or SearchRequest())
    total = 0
    if where is not None:
        clause, args = where.sql()
        total = cat.conn.execute(
            f"SELECT COUNT(*) FROM resources r WHERE {clause}", args
        ).fetchone()[0]
    return {
        "total": total,
        "axes": [{"id": k, "name": v} for k, v in AXES.items()],
        "hints": hints,
    }


__all__ = ["AXES", "InvalidNode", "axes_overview", "register_domain", "tree_children"]
