"""CKAN 通用适配器：一个类服务所有 CKAN 门户，新增门户只在 sources.yaml 加一行。

CKAN Action API：GET {base_url}/package_search?q=&rows=
坑（取自 ckan-mcp-server 与 Dataseek 的注释）：
- HTTP 200 但 `success:false`：必须当错误，不能当空结果；
- `q` 里的冒号会被当成字段查询，导致空结果或 409，所以清掉；
- 多语言门户（如加拿大）的标题在 `title_translated`，`title` 可能为空；
- 各门户落地页地址格式不同，用 `url_pattern` 配置（{id} {name}）。

sources.yaml 配置示例：
  - id: hdx
    protocol: ckan
    base_url: https://data.humdata.org/api/3/action
    options: {url_pattern: "https://data.humdata.org/dataset/{name}"}
"""

from __future__ import annotations

from typing import Any

from ..engine.errors import ShapeError, SourceError
from ..models import Dataset, Resource
from .base import Ctx, EnglishSource, SourceSpec, clean_text

_LANG_ORDER = ("en", "zh", "fr")


def _localized(value: Any, fallback: str | None = None) -> str | None:
    """CKAN 多语言字段：可能是字符串，也可能是 {lang: text}。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for lang in _LANG_ORDER:
            if isinstance(value.get(lang), str) and value[lang].strip():
                return value[lang].strip()
        for v in value.values():
            if isinstance(v, str) and v.strip():
                return v.strip()
    return fallback


def to_dataset(pkg: dict[str, Any], source_id: str, url_pattern: str) -> Dataset | None:
    title = _localized(pkg.get("title")) or _localized(pkg.get("title_translated"))
    name = str(pkg.get("name") or "")
    url = url_pattern.format(id=pkg.get("id", ""), name=name)
    if not title or not name or not url.startswith("http"):
        return None
    resources: list[Resource] = []
    for r in pkg.get("resources") or []:
        r_url = r.get("url")
        if not isinstance(r_url, str) or not r_url.startswith("http"):
            continue
        fmt = (r.get("format") or "").strip().upper() or None
        resources.append(
            Resource(
                "api" if fmt in ("API", "WMS", "WFS") else "file",
                _localized(r.get("name")) or fmt or "资源",
                r_url,
                fmt,
                r.get("size") if isinstance(r.get("size"), int) else None,
            )
        )
    org = pkg.get("organization") or {}
    return Dataset(
        title=title,
        url=url,
        source=source_id,
        description=clean_text(
            _localized(pkg.get("notes")) or _localized(pkg.get("notes_translated"))
        ),
        publisher=_localized(org.get("title")) if isinstance(org, dict) else None,
        license=pkg.get("license_id") or pkg.get("license_title") or None,
        updated=(pkg.get("metadata_modified") or "")[:10] or None,
        formats=list(dict.fromkeys(r.format for r in resources if r.format))[:8],
        resources=resources[:20],
    )


class CkanSource(EnglishSource):
    def __init__(self, spec: SourceSpec) -> None:
        super().__init__(spec)
        if not spec.base_url:
            raise ValueError(f"CKAN 来源 {spec.id} 缺少 base_url")
        self.url_pattern = str(spec.options.get("url_pattern", ""))
        if not self.url_pattern:
            raise ValueError(f"CKAN 来源 {spec.id} 缺少 options.url_pattern")

    async def fetch(self, ctx: Ctx, query: str, limit: int) -> list[Dataset]:
        params = {"q": query.replace(":", " "), "rows": min(limit, 50)}
        j = await ctx.http.get_json(f"{self.spec.base_url}/package_search", params=params)
        if not isinstance(j, dict) or "success" not in j:
            raise ShapeError("CKAN 响应缺少 success 字段")
        if not j["success"]:
            raise SourceError("http", f"CKAN 返回 success=false：{j.get('error')}")
        results = (j.get("result") or {}).get("results")
        if not isinstance(results, list):
            raise ShapeError("CKAN 响应缺少 result.results")
        return [d for p in results if (d := to_dataset(p, self.id, self.url_pattern))]
