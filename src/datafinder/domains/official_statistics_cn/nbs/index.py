"""国家统计局指标索引：把"分类树 → 指标"逐级爬到本地，之后按关键词秒查。

为什么需要：官方接口只能按分类树逐级点，没有"按关键词搜指标"（全局搜索只给全国级、且模糊）。
有了本地索引，"河北 工业 质量"能直接定位到 分省年度数据 里的相关指标，再用 `nbs_fetch` 取表。

爬取是**用户主动触发**的一次性动作（`datafinder nbs index`），遵守这些规则：
- 串行、间隔 ≥1 秒；
- 一旦遇到访问防护验证页就立刻停下并保存进度，之后用同一条命令继续，不重试、不绕过；
- 只存指标名称、单位、所在分类路径与统计口径，不存任何数值。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from datafinder.config import data_dir
from datafinder.engine.errors import SourceError
from datafinder.query.units import balanced_pick, build_units, combine, prefilter_terms, unit_scores

from .client import DATABASES, NbsClient

# 默认索引的库：年度、分省年度、主要城市年度（论文最常用）；其余库可用 --db 追加
DEFAULT_DBS = ["3", "6", "8"]
REGIONAL_DBS = {"4", "5", "6", "7", "8", "9", "10"}  # 有地区维度的库（其余为全国口径）

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ind (
  db TEXT NOT NULL, cid TEXT NOT NULL, root_id TEXT NOT NULL, path TEXT NOT NULL,
  ind_id TEXT NOT NULL, name TEXT NOT NULL, unit TEXT, mark TEXT,
  PRIMARY KEY (db, cid, ind_id)
);
CREATE INDEX IF NOT EXISTS ind_db ON ind(db);
CREATE TABLE IF NOT EXISTS done (db TEXT NOT NULL, cid TEXT NOT NULL, PRIMARY KEY (db, cid));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

def index_path() -> Path:
    return data_dir() / "nbs_index.db"


def connect_index(path: Path | None = None) -> sqlite3.Connection:
    p = path or index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def stats(conn: sqlite3.Connection) -> dict:
    per_db = {
        r["db"]: r["n"] for r in conn.execute("SELECT db, COUNT(*) AS n FROM ind GROUP BY db")
    }
    built = conn.execute("SELECT value FROM meta WHERE key='updated'").fetchone()
    return {
        "indicators": sum(per_db.values()),
        "databases": {code: {"name": DATABASES[code][0], "indicators": n} for code, n in per_db.items()},
        "updated": built["value"] if built else None,
    }


# ───────────── 建索引 ─────────────


@dataclass
class BuildResult:
    status: str  # done | paused | limited
    leaves_done: int
    indicators_added: int
    requests: int
    message: str = ""


async def build(
    client: NbsClient,
    conn: sqlite3.Connection,
    codes: list[str],
    *,
    progress: Callable[[str], None] | None = None,
    max_requests: int | None = None,
) -> BuildResult:
    """深度优先走分类树；每个叶子数据集取一次指标清单。已完成的叶子会跳过，所以可以反复续跑。"""
    leaves = added = 0
    start_calls = client.calls
    say = progress or (lambda _m: None)

    def budget_left() -> bool:
        return max_requests is None or client.calls - start_calls < max_requests

    async def walk(code: str, pid: str, path: list[str]) -> str | None:
        nonlocal leaves, added
        nodes = await client.tree(code, pid)
        for node in nodes:
            # 每个库只有一个与库同名的根节点：路径里不重复出现
            here = path if (node["name"] == path[-1] and len(path) == 1) else [*path, node["name"]]
            if node["is_leaf"]:
                if conn.execute(
                    "SELECT 1 FROM done WHERE db=? AND cid=?", (code, node["id"])
                ).fetchone():
                    continue
                if not budget_left():
                    return "limited"
                inds = await client.indicators(node["id"])
                conn.executemany(
                    "INSERT OR REPLACE INTO ind(db,cid,root_id,path,ind_id,name,unit,mark)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    [
                        (code, node["id"], node["root_id"], " › ".join(here), i["id"],
                         i["name"], i.get("unit"), i.get("mark"))
                        for i in inds
                    ],
                )
                conn.execute("INSERT OR REPLACE INTO done(db,cid) VALUES (?,?)", (code, node["id"]))
                conn.commit()
                leaves += 1
                added += len(inds)
                say(f"{DATABASES[code][0]} · {' › '.join(here)}（{len(inds)} 个指标）")
            else:
                if not budget_left():
                    return "limited"
                stop = await walk(code, node["id"], here)
                if stop:
                    return stop
        return None

    stop: str | None = None
    try:
        for code in codes:
            if code not in DATABASES:
                raise ValueError(f"未知数据库代码 {code}")
            stop = await walk(code, "", [DATABASES[code][0]])
            if stop:
                break
    except SourceError as exc:
        conn.commit()
        return BuildResult("paused", leaves, added, client.calls - start_calls, f"{exc}。已保存进度，稍后重新运行即可继续。")
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES ('updated', datetime('now'))"
    )
    conn.commit()
    if stop == "limited":
        return BuildResult("limited", leaves, added, client.calls - start_calls, "已达到本次请求上限，重新运行可继续。")
    return BuildResult("done", leaves, added, client.calls - start_calls)


# ───────────── 查询 ─────────────

PER_DATASET = 2  # 同一个数据集（cid）最多出几个指标：避免"食品制造业/家具制造业/…"同模板指标刷屏


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    dbs: list[str] | None = None,
    regional: bool | None = None,
    limit: int = 20,
    per_dataset: int = PER_DATASET,
) -> list[dict]:
    """按"概念单元"（含统计术语同义词）给指标名与分类路径打分，并按数据集去重。

    regional=True 只看有地区维度的库（分省、城市），False 只看全国口径，None 不限。
    """
    units = build_units(query)
    terms = prefilter_terms(units)
    if not units or not terms:
        return []
    like = " OR ".join(["name LIKE ? OR path LIKE ?"] * len(terms))
    args: list = [x for t in terms for x in (f"%{t}%", f"%{t}%")]
    sql = f"SELECT * FROM ind WHERE ({like})"
    if dbs:
        sql += f" AND db IN ({','.join('?' * len(dbs))})"
        args += dbs
    scored: list[tuple[float, list[float], sqlite3.Row]] = []
    for row in conn.execute(sql, args):
        if regional is True and row["db"] not in REGIONAL_DBS:
            continue
        if regional is False and row["db"] in REGIONAL_DBS:
            continue
        vec = unit_scores(units, row["name"], row["path"])
        score = combine(units, vec)
        if score <= 0:
            continue
        scored.append((score - 0.001 * len(row["name"]), vec, row))  # 同分偏好更短、更通用的名称
    scored.sort(key=lambda x: -x[0])
    # 先按数据集去重（同模板指标刷屏），再保证每个概念都有代表
    seen: set[tuple[str, str]] = set()
    per_cid: dict[tuple[str, str], int] = {}
    kept: list[tuple[float, list[float], sqlite3.Row]] = []
    for item in scored:
        r = item[2]
        key, cid_key = (r["db"], r["name"]), (r["db"], r["cid"])
        if key in seen or per_cid.get(cid_key, 0) >= per_dataset:
            continue
        seen.add(key)
        per_cid[cid_key] = per_cid.get(cid_key, 0) + 1
        kept.append(item)
    picked = balanced_pick(kept, [k[1] for k in kept], limit)
    return [
        {
            "database": DATABASES[r["db"]][0],
            "db": r["db"],
            "regional": r["db"] in REGIONAL_DBS,
            "path": r["path"],
            "indicator": r["name"],
            "indicator_id": r["ind_id"],
            "cid": r["cid"],
            "root_id": r["root_id"],
            "unit": r["unit"],
            "mark": r["mark"],
            "score": round(score, 3),
        }
        for score, _vec, r in picked
    ]
