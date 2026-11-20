"""整体状态：门户库、指标索引、年鉴目录、导出目录。"""

from __future__ import annotations

from typing import Any

from .. import __version__
from ..nbs import index as nbs_index
from ..sources.dataseek import find_executable
from ..store import connect, db_path, get_meta, portal_count
from ..tables import exports_dir

# ───────────── 状态 ─────────────


def status() -> dict[str, Any]:
    conn = connect()
    try:
        portals = portal_count(conn)
        synced = get_meta(conn, "synced_at")
    finally:
        conn.close()
    idx = nbs_index.connect_index()
    try:
        index = nbs_index.stats(idx)
    finally:
        idx.close()
    return {
        "version": __version__,
        "portals": portals,
        "portals_synced_at": synced,
        "portals_hint": None if portals else "门户库为空，请先在终端运行 datafinder sync",
        "nbs_index": index,
        "nbs_index_hint": None
        if index["indicators"]
        else "国家统计局指标索引尚未建立：在终端运行 datafinder nbs index（约需十几分钟，只需一次）",
        "dataseek": bool(find_executable()),
        "exports_dir": str(exports_dir()),
        "database": str(db_path()),
    }
