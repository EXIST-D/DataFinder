"""Zenodo（CERN 运营的科研数据仓库）。官方 REST API：GET /api/records。

坑（取自 Dataseek 与官方文档）：匿名每页最多 25 条，超过返回 400；每分钟约 60 次；
Lucene 语法出错会返回 500——我们的检索词只有普通单词，不带运算符。
"""

from __future__ import annotations

import os
from typing import Any

from ..engine.errors import ShapeError
from ..models import Dataset, Resource
from .base import Ctx, EnglishSource, clean_text

URL = "https://zenodo.org/api/records"
MAX_PAGE = 25


def _ext(name: str) -> str | None:
    return name.rsplit(".", 1)[-1].upper() if "." in name else None


def to_dataset(hit: dict[str, Any]) -> Dataset | None:
    meta = hit.get("metadata") or {}
    title = (meta.get("title") or hit.get("title") or "").strip()
    url = (hit.get("links") or {}).get("self_html") or (
        f"https://zenodo.org/records/{hit['id']}" if hit.get("id") else ""
    )
    if not title or not url.startswith("http"):
        return None
    is_open = meta.get("access_right", "open") == "open"
    license_id = (meta.get("license") or {}).get("id")
    files = (hit.get("files") or []) if is_open else []
    resources = [
        Resource(
            "file",
            f["key"],
            (f.get("links") or {}).get("self", ""),
            _ext(f["key"]),
            f.get("size"),
        )
        for f in files
        if f.get("key") and (f.get("links") or {}).get("self")
    ]
    creators = [c.get("name") for c in (meta.get("creators") or []) if c.get("name")]
    aliases = [d for d in (hit.get("conceptdoi"),) if d]
    return Dataset(
        title=title,
        url=url,
        source="zenodo",
        description=clean_text(meta.get("description")),
        publisher="; ".join(creators[:3]) or None,
        doi=hit.get("doi") or meta.get("doi"),
        license=license_id if is_open else (license_id or "受限访问"),
        updated=(hit.get("updated") or "")[:10] or None,
        formats=list(dict.fromkeys(f for r in resources if (f := r.format))),
        size_bytes=sum(r.size_bytes or 0 for r in resources) or None,
        resources=resources,
        aliases=aliases,
    )


class ZenodoSource(EnglishSource):
    async def fetch(self, ctx: Ctx, query: str, limit: int) -> list[Dataset]:
        params: dict[str, Any] = {
            "q": query,
            "type": "dataset",
            "size": min(limit, MAX_PAGE),
            "sort": "bestmatch",
        }
        headers = {}
        if token := os.environ.get("ZENODO_TOKEN"):  # 可选：提高限额
            headers["Authorization"] = f"Bearer {token}"
        j = await ctx.http.get_json(URL, params=params, headers=headers)
        hits = (j.get("hits") or {}).get("hits") if isinstance(j, dict) else None
        if not isinstance(hits, list):
            raise ShapeError("Zenodo 响应缺少 hits.hits")
        return [d for h in hits if (d := to_dataset(h))]
