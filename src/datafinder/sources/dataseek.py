"""Dataseek 桥接：用一个可选的外部程序覆盖长尾来源（约 60 个国际数据源）。

Dataseek（https://github.com/lmarkmann/dataseek，Apache-2.0/MIT）是 Rust 写的跨源数据集搜索 CLI。
我们只把它当作"又一个来源"：
- 吃我们翻译好的**英文检索词**（它自己对中文无能为力，实测纯中文查询召回很差甚至为 0）；
- 用 `-x` 排除我们已自研适配器覆盖的来源，避免重复请求；
- 返回的结果仍进入我们的去重与排序（带中文感知的覆盖度），而不是直接信它的排序。

安全：子进程用参数数组，不经过 shell；限制超时和输出大小。没装也不影响其他来源。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from ..engine.errors import ShapeError, SourceError
from ..models import Dataset, Resource
from ..query.plan import QueryPlan
from .base import Ctx, EnglishSource, SourceSpec, clean_text

MAX_STDOUT = 8 * 1024 * 1024
INTERNAL_DEADLINE = 12  # 秒，传给 dsk --timeout；外层超时要比它略长


def find_executable() -> str | None:
    """优先找与本程序同一环境里的 dsk（uv tool 安装时它不在 PATH 上）。"""
    sibling = Path(sys.executable).parent / "dsk"
    if sibling.exists():
        return str(sibling)
    return shutil.which("dsk") or shutil.which("dataseek")


def to_dataset(r: dict[str, Any]) -> Dataset | None:
    title = (r.get("title") or "").strip()
    url = r.get("url") or ""
    if not title or not url.startswith("http"):
        return None
    return Dataset(
        title=title,
        url=url,
        source="dataseek",
        description=clean_text(r.get("description")),
        publisher=r.get("publisher"),
        doi=r.get("doi"),
        license=r.get("license"),
        updated=r.get("updated"),
        formats=[str(f).upper() for f in (r.get("formats") or [])][:8],
        size_bytes=r.get("size_bytes"),
        aliases=list(r.get("aliases") or []),
        sources=list(r.get("sources") or []),  # 底层仓库名，用于展示出处
    )


_MIME = {
    "text/csv": "CSV",
    "application/json": "JSON",
    "application/zip": "ZIP",
    "application/pdf": "PDF",
    "application/vnd.ms-excel": "XLS",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
    "application/x-netcdf": "NETCDF",
    "application/xml": "XML",
    "text/plain": "TXT",
    "text/tab-separated-values": "TSV",
    "application/x-rar-compressed": "RAR",
    "application/x-7z-compressed": "7Z",
    "application/gzip": "GZ",
    "application/x-tar": "TAR",
    "application/geo+json": "GEOJSON",
    "application/vnd.google-earth.kml+xml": "KML",
}


def _format_for(mime: str, name: str) -> str | None:
    """MIME → 常见格式名；MIME 不明（如 octet-stream）时看文件扩展名。"""
    known = _MIME.get(mime.lower())
    if known:
        return known
    ext = name.rsplit(".", 1)[-1].upper() if "." in name else None
    if ext and 1 < len(ext) <= 8:
        return ext
    sub = mime.split("/")[-1].removeprefix("x-").upper()
    return None if sub in ("", "OCTET-STREAM") else sub


def _resource_name(url: str) -> str:
    parts = [p for p in url.split("?")[0].split("/") if p]
    if parts and parts[-1] == "content" and len(parts) > 1:  # Zenodo: .../files/名字/content
        return parts[-2]
    return parts[-1] if parts else url


async def inspect_url(url: str, *, timeout: float = 20.0) -> list[Resource]:
    """用 `dsk inspect` 读取一个数据集页面公布的文件列表（按需调用，不在搜索阶段做）。"""
    exe = find_executable()
    if exe is None:
        raise SourceError("down", "未安装 Dataseek，无法读取文件列表")
    proc = await asyncio.create_subprocess_exec(
        exe,
        "inspect",
        url,
        "--json",
        "-q",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["（无输出）"]
        raise SourceError("http", f"读取文件列表失败：{tail[0][:200]}")
    if len(out) > MAX_STDOUT:
        raise ShapeError("dsk 输出过大")
    try:
        data = json.loads(out)
    except ValueError as exc:
        raise ShapeError(f"dsk 输出不是 JSON：{exc}") from exc
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        raise ShapeError("dsk inspect 输出缺少 files")
    out_res: list[Resource] = []
    for f in files:
        furl = f.get("url") if isinstance(f, dict) else None
        if not isinstance(furl, str) or not furl.startswith("http"):
            continue
        mime = str(f.get("format") or "")
        name = str(f.get("name") or _resource_name(furl))
        out_res.append(
            Resource(
                "file",
                name,
                furl,
                _format_for(mime, name),
                f.get("size") if isinstance(f.get("size"), int) else None,
            )
        )
    return out_res


class DataseekSource(EnglishSource):
    def __init__(self, spec: SourceSpec, exclude: list[str] | None = None) -> None:
        super().__init__(spec)
        self.exclude = sorted(set(exclude or []))
        self.exe = find_executable()

    def supports(self, plan: QueryPlan) -> bool:
        return self.exe is not None and super().supports(plan)

    def cache_query(self, plan: QueryPlan) -> str:
        return f"{super().cache_query(plan)}|x={','.join(self.exclude)}"

    async def fetch(self, ctx: Ctx, query: str, limit: int) -> list[Dataset]:
        assert self.exe
        cmd = [
            self.exe,
            "search",
            query,
            "--json",
            "-q",
            "-n",
            str(limit),
            "--timeout",
            str(INTERNAL_DEADLINE),
        ]
        if self.exclude:
            cmd += ["-x", ",".join(self.exclude)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), INTERNAL_DEADLINE + 5)
        except (TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["（无输出）"]
            raise SourceError("down", f"dsk 退出码 {proc.returncode}：{tail[0][:200]}")
        if len(out) > MAX_STDOUT:
            raise ShapeError("dsk 输出过大")
        try:
            data = json.loads(out)
        except ValueError as exc:
            raise ShapeError(f"dsk 输出不是 JSON：{exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ShapeError("dsk 输出缺少 results（可能是版本不兼容）")
        return [d for r in data["results"] if (d := to_dataset(r))]
