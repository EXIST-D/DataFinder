"""目录快照：把整个目录库打包成可发布的一对文件，用户一条命令装好，不必自己重新抓取。

快照 = `datafinder-catalog.db.gz`（SQLite 目录库，gzip）+ `datafinder-catalog.json`（清单：版本、校验和、数量）。
目录里只有名称/单位/口径/链接等元数据，不含任何数值，可公开发布。
安装是整体替换（旧库先备份为 *.bak）；程序与快照的库版本不一致时拒绝安装。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .. import __version__
from ..config import USER_AGENT
from .adapters import AdapterError
from .download import check_url
from .schema import SCHEMA_VERSION, db_path

SNAPSHOT_FORMAT = 1
DB_NAME = "datafinder-catalog.db.gz"
MANIFEST_NAME = "datafinder-catalog.json"
DEFAULT_BASE_URL = "https://github.com/EXIST-D/DataFinder/releases/download/catalog-latest"
MAX_BYTES = 1024 * 1024 * 1024
TIMEOUT = 60.0


class SnapshotError(AdapterError):
    """快照制作/安装失败（消息直接给用户看）。"""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _inspect(path: Path) -> dict:
    """打开目录库做体检，返回 {schema_version, resources, by_source, last_indexed}。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SnapshotError("目录库未通过完整性检查")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        by_source = dict(
            conn.execute("SELECT source_id, COUNT(*) FROM resources GROUP BY source_id").fetchall()
        )
        last = conn.execute("SELECT MAX(indexed_at) FROM resources").fetchone()[0]
        return {
            "schema_version": version,
            "resources": sum(by_source.values()),
            "by_source": by_source,
            "last_indexed": last,
        }
    except sqlite3.DatabaseError as e:
        raise SnapshotError(f"不是有效的目录库：{e}") from e
    finally:
        conn.close()


def build_snapshot(out_dir: Path, *, source: Path | None = None) -> dict:
    """把当前目录库打包到 out_dir，返回清单。"""
    src = source or db_path()
    if not src.exists():
        raise SnapshotError("目录库不存在：先运行 datafinder index build（或 index pull）")
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "catalog.tmp.db"
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("VACUUM INTO ?", (str(tmp),))  # 干净的一份拷贝，不带 WAL 与碎片
    finally:
        conn.close()
    try:
        info = _inspect(tmp)
        if info["schema_version"] != SCHEMA_VERSION or not info["resources"]:
            raise SnapshotError("目录库为空或版本不符，不能制作快照")
        gz = out_dir / DB_NAME
        with tmp.open("rb") as f_in, gzip.GzipFile(gz, "wb", compresslevel=9, mtime=0) as f_out:
            shutil.copyfileobj(f_in, f_out, 1 << 20)
        manifest = {
            "format": SNAPSHOT_FORMAT,
            "schema_version": info["schema_version"],
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "app_version": __version__,
            "file": DB_NAME,
            "bytes": gz.stat().st_size,
            "bytes_uncompressed": tmp.stat().st_size,
            "sha256": _sha256(gz),
            "resources": info["resources"],
            "last_indexed": info["last_indexed"],
            "by_source": info["by_source"],
        }
    finally:
        tmp.unlink(missing_ok=True)
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _check_manifest(m: dict) -> None:
    if not isinstance(m, dict) or m.get("format") != SNAPSHOT_FORMAT:
        raise SnapshotError(
            f"快照格式 {m.get('format') if isinstance(m, dict) else '?'} 不被当前程序支持，请升级 datafinder"
        )
    if m.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotError(
            f"快照的库版本 {m.get('schema_version')} 与程序要求的 {SCHEMA_VERSION} 不一致："
            "请升级 datafinder，或用 datafinder index build 自行抓取"
        )


def local_state(path: Path | None = None) -> dict | None:
    """本地目录库的概况；不存在或为空返回 None。"""
    p = path or db_path()
    if not p.exists():
        return None
    try:
        info = _inspect(p)
    except SnapshotError:
        return None
    return info if info["resources"] else None


def _fetch(
    base: str, name: str, dest: Path, *, transport: httpx.BaseTransport | None, progress
) -> None:
    url = f"{base.rstrip('/')}/{name}"
    if not url.startswith("https://"):
        raise SnapshotError(f"快照地址必须是 https：{url}")
    check_url(url)

    def guard(request: httpx.Request) -> None:  # 每一跳（含重定向）都检查
        try:
            check_url(str(request.url))
        except AdapterError as e:
            raise SnapshotError(str(e)) from e

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
        max_redirects=5,
        transport=transport,
        event_hooks={"request": [guard]},
    ) as c:
        try:
            with c.stream("GET", url) as r:
                if r.status_code == 404:
                    raise SnapshotError(f"还没有发布快照：{url}")
                if r.status_code >= 400:
                    raise SnapshotError(f"下载失败（HTTP {r.status_code}）：{url}")
                total = int(r.headers.get("content-length") or 0)
                size = 0
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise SnapshotError("快照超过 1GB 上限，已中止")
                        f.write(chunk)
                        if progress and total:
                            progress(f"已下载 {size * 100 // total}%（{size >> 20}MB）")
        except httpx.HTTPError as e:
            raise SnapshotError(f"连不上快照地址：{e}") from e


def fetch_manifest(source: str, *, transport: httpx.BaseTransport | None = None) -> dict:
    """读取快照清单（source 是 https 基地址或本地目录）。"""
    if source.startswith("https://") or source.startswith("http://"):
        tmp = db_path().parent / "manifest.part"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            _fetch(source, MANIFEST_NAME, tmp, transport=transport, progress=None)
            text = tmp.read_text(encoding="utf-8")
        finally:
            tmp.unlink(missing_ok=True)
    else:
        p = Path(source).expanduser() / MANIFEST_NAME
        if not p.exists():
            raise SnapshotError(f"目录里没有 {MANIFEST_NAME}：{source}")
        text = p.read_text(encoding="utf-8")
    try:
        manifest = json.loads(text)
    except ValueError as e:
        raise SnapshotError("快照清单不是有效 JSON") from e
    _check_manifest(manifest)
    return manifest


def install_snapshot(
    source: str,
    *,
    force: bool = False,
    transport: httpx.BaseTransport | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """下载（或读取本地）快照，校验后整体替换本地目录库。返回 {manifest, backup, replaced}。"""
    manifest = fetch_manifest(source, transport=transport)
    target = db_path()
    local = local_state(target)
    if local and not force and (local["last_indexed"] or "") > manifest["created_at"]:
        raise SnapshotError(
            f"本地目录（{local['last_indexed']}）比快照（{manifest['created_at']}）更新，"
            "直接替换会丢掉你自己抓取的内容。确认要换请加 --force"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    gz = target.parent / "snapshot.part.gz"
    new_db = target.with_name(target.name + ".new")
    try:
        if source.startswith(("https://", "http://")):
            _fetch(source, manifest["file"], gz, transport=transport, progress=progress)
        else:
            src_file = Path(source).expanduser() / manifest["file"]
            if not src_file.exists():
                raise SnapshotError(f"缺少数据文件：{src_file}")
            shutil.copyfile(src_file, gz)
        if _sha256(gz) != manifest["sha256"]:
            raise SnapshotError("校验和不一致：文件损坏或被篡改，已放弃安装")
        if progress:
            progress("解压并检查")
        with gzip.open(gz, "rb") as f_in, new_db.open("wb") as f_out:
            shutil.copyfileobj(f_in, f_out, 1 << 20)
        info = _inspect(new_db)
        if info["schema_version"] != SCHEMA_VERSION or info["resources"] != manifest["resources"]:
            raise SnapshotError("快照内容与清单不符，已放弃安装")
        backup = None
        if target.exists():
            backup = target.with_name(target.name + ".bak")
            os.replace(target, backup)
        for ext in ("-wal", "-shm"):
            target.with_name(target.name + ext).unlink(missing_ok=True)
        os.replace(new_db, target)
    finally:
        gz.unlink(missing_ok=True)
        new_db.unlink(missing_ok=True)
    return {"manifest": manifest, "backup": str(backup) if backup else None}
