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
from ..models import Dataset
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


class DataseekSource(EnglishSource):
    def __init__(self, spec: SourceSpec, exclude: list[str] | None = None) -> None:
        super().__init__(spec)
        self.exclude = sorted(set(exclude or []))
        self.exe = find_executable()

    def supports(self, plan: QueryPlan) -> bool:
        return self.exe is not None and super().supports(plan)

    def cache_query(self, plan: QueryPlan) -> str:
        return f"{plan.query_en()}|x={','.join(self.exclude)}"

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
