"""整体状态：资源目录、门户库、导出目录、可迁移的旧索引。"""

from __future__ import annotations

from typing import Any

from datafinder import __version__
from datafinder.catalog import Catalog
from datafinder.catalog.adapters import adapters
from datafinder.catalog.schema import db_path as catalog_path
from datafinder.sources.dataseek import find_executable
from datafinder.store import connect, db_path, get_meta, portal_count
from datafinder.tables import exports_dir


def status() -> dict[str, Any]:
    conn = connect()
    try:
        portals, synced = portal_count(conn), get_meta(conn, "synced_at")
    finally:
        conn.close()
    with Catalog.open() as cat:
        info = cat.stats()
    migratable = sorted(a.id for a in adapters().values() if a.legacy_present())
    hints = []
    if not portals:
        hints.append("门户库为空：运行 datafinder sync")
    if not info["resources"]:
        hints.append("资源目录为空：运行 datafinder index build 抓取；有旧版本地索引则运行 datafinder index migrate 一次性迁入")
    elif migratable:
        hints.append(f"发现旧版本地索引（{', '.join(migratable)}）尚未迁入：运行 datafinder index migrate")
    return {
        "version": __version__,
        "catalog": info,
        "portals": portals,
        "portals_synced_at": synced,
        "migratable_adapters": migratable,
        "dataseek": bool(find_executable()),
        "exports_dir": str(exports_dir()),
        "catalog_db": str(catalog_path()),
        "portals_db": str(db_path()),
        "hints": hints,
    }
