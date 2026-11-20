"""国家统计局指标索引：把"分类树 → 指标"逐级爬到本地，之后按关键词秒查。

为什么需要：官方接口只能按分类树逐级点，没有"按关键词搜指标"（全局搜索只给全国级、且模糊）。
有了本地索引，"河北 工业 质量"能直接定位到 分省年度数据 里的相关指标，再用 `nbs_fetch` 取表。

爬取是**用户主动触发**的一次性动作（`datafinder nbs index`），遵守这些规则：
- 串行、间隔 ≥1 秒；
- 一旦遇到访问防护验证页就立刻停下并保存进度，之后用同一条命令继续，不重试、不绕过；
- 只存指标名称、单位、所在分类路径与统计口径，不存任何数值。
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import data_dir
from .engine.errors import SourceError
from .query.plan import parse
from .query.tokenize import tokenize
from .sources.nbs_tree import DATABASES, NbsClient

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

# 查询里没有区分度的词：地区范围、时间、"评价/分析"等泛词
_GENERIC = re.compile(
    r"(各省|分省|全国|各地|地区|省份|城市|各市|近[0-9一二三四五六七八九十]+年|历年|年度|评价|体系|分析|研究|情况|水平|"
    r"数据|指标|统计|论文|的|与|和|及)"
)


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


def _query_grams(query: str) -> tuple[set[str], list[str]]:
    plan = parse(query)
    text = query
    for name in plan.location_zh:  # 地名不属于指标名
        text = text.replace(name, " ")
    for r in plan.regions:
        text = text.replace(r, " ")
    text = _GENERIC.sub(" ", text)
    grams = {g for g in tokenize(text) if len(g) == 2 or len(g) == 1}
    words = [c.zh for c in plan.concepts if c.zh and not c.dimension and not c.weak]
    return grams, [w for w in words if len(w) >= 2]


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    dbs: list[str] | None = None,
    regional: bool | None = None,
    limit: int = 20,
) -> list[dict]:
    """按名称（权重高）与分类路径（权重低）的二元组覆盖度排序。

    regional=True 只看有地区维度的库（分省、城市），False 只看全国口径，None 不限。
    """
    grams, words = _query_grams(query)
    if not grams:
        return []
    sql = "SELECT * FROM ind"
    args: list = []
    if dbs:
        sql += f" WHERE db IN ({','.join('?' * len(dbs))})"
        args = list(dbs)
    out: list[tuple[float, sqlite3.Row]] = []
    for row in conn.execute(sql, args):
        if regional is True and row["db"] not in REGIONAL_DBS:
            continue
        if regional is False and row["db"] in REGIONAL_DBS:
            continue
        name_g = set(tokenize(row["name"]))
        hit_name = len(grams & name_g)
        hit_path = len(grams & set(tokenize(row["path"]))) - hit_name if hit_name == 0 else 0
        if hit_name == 0 and hit_path <= 0:
            continue
        score = (2 * hit_name + max(hit_path, 0)) / (2 * len(grams))
        score += 0.5 * sum(1 for w in words if w in row["name"])  # 整词出现在指标名里
        score -= 0.001 * len(row["name"])  # 同分时偏好更短、更通用的指标名
        out.append((score, row))
    out.sort(key=lambda x: -x[0])
    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    for score, r in out:
        key = (r["db"], r["name"])
        if key in seen:
            continue
        seen.add(key)
        result.append(
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
        )
        if len(result) >= limit:
            break
    return result
