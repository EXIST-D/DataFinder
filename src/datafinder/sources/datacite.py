"""DataCite：全球 DOI 注册机构，覆盖上千个数据仓库——含中国的科学数据银行（ScienceDB，DOI 前缀 10.57760）。

坑（取自 Dataseek）：不加 `sort=relevance` 就按最近更新排序；版本 DOI 要用 concept DOI 做别名，
否则同一数据的多个版本会各占一条；描述里常有 HTML。
"""

from __future__ import annotations

from typing import Any

from ..engine.errors import ShapeError
from ..models import Dataset
from .base import Ctx, EnglishSource, clean_text

URL = "https://api.datacite.org/dois"
_VERSION_RELATIONS = {"IsVersionOf", "IsIdenticalTo", "IsVariantFormOf"}


def to_dataset(item: dict[str, Any]) -> Dataset | None:
    a = item.get("attributes") or {}
    titles = a.get("titles") or []
    title = (titles[0].get("title") if titles else "") or ""
    doi = a.get("doi")
    url = a.get("url") or ""
    if not url.startswith("http") and doi:
        url = f"https://doi.org/{doi}"
    if not title.strip() or not url.startswith("http"):
        return None
    descs = a.get("descriptions") or []
    rights = a.get("rightsList") or []
    right = rights[0] if rights else {}
    publisher = a.get("publisher")
    if isinstance(publisher, dict):
        publisher = publisher.get("name")
    aliases = [
        r["relatedIdentifier"]
        for r in (a.get("relatedIdentifiers") or [])
        if r.get("relatedIdentifierType") == "DOI"
        and r.get("relationType") in _VERSION_RELATIONS
        and r.get("relatedIdentifier")
    ]
    return Dataset(
        title=title.strip(),
        url=url,
        source="datacite",
        description=clean_text(descs[0].get("description")) if descs else None,
        publisher=publisher,
        doi=doi,
        license=right.get("rightsIdentifier") or right.get("rights"),
        updated=(a.get("updated") or "")[:10] or None,
        formats=[str(f).upper() for f in (a.get("formats") or []) if f][:6],
        aliases=aliases,
    )


class DataCiteSource(EnglishSource):
    async def fetch(self, ctx: Ctx, query: str, limit: int) -> list[Dataset]:
        params = {
            "query": query,
            "resource-type-id": "dataset",
            "page[size]": min(limit, 100),
            "sort": "relevance",
        }
        j = await ctx.http.get_json(URL, params=params)
        data = j.get("data") if isinstance(j, dict) else None
        if not isinstance(data, list):
            raise ShapeError("DataCite 响应缺少 data")
        return [d for item in data if (d := to_dataset(item))]
