"""检视一个资源：层级、分发入口与获取方式、取数/下载需要什么。"""

from __future__ import annotations

from typing import Any

from datafinder.catalog import Entry, SearchRequest, kinds
from datafinder.catalog.search import summarize

from .common import open_catalog
from .errors import ServiceError, guard

_REQ = SearchRequest()


def _brief(e: Entry) -> dict[str, Any]:
    return summarize(e, units=[], req=_REQ, score=0.0)


def _needs(e: Entry) -> list[str]:
    """generate 类资源取数时还需要用户给什么参数。"""
    needs = []
    if e.kind == "dataset":
        needs.append("indicators")
    if any(
        r.scope_mode == "descendants"
        and r.relation == "covers"
        and r.granularity in ("province", "prefecture")
        for r in e.regions
    ):
        needs.append("regions")
    return needs


def inspect(
    resource_id: str, *, children: bool = False, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    with guard(), open_catalog() as cat:
        e = cat.get(resource_id)
        if e is None:
            raise ServiceError(
                f"目录里没有资源 {resource_id!r}",
                code="NOT_FOUND",
                next_action="用 search 找到资源，再用返回的 resource_id",
            )
        parents: list[dict[str, Any]] = []
        p = cat.get(e.parent_id) if e.parent_id else None
        while p is not None:
            parents.append({"resource_id": p.resource_id, "kind": p.kind, "title": p.title})
            p = cat.get(p.parent_id) if p.parent_id else None
        out: dict[str, Any] = {
            "resource": _brief(e),
            "parents": list(reversed(parents)),
            "child_count": cat.child_count(resource_id),
            "distributions": [
                {
                    "access_method": d.access_method,
                    "delivery": d.delivery,
                    "url": d.url,
                    "file_format": d.file_format,
                    "usability": d.usability,
                    "access_status": d.access_status,
                    "note": d.note,
                }
                for d in e.distributions
            ],
            "verification_level": e.verification_level,
            "extra": {k: v for k, v in e.extra.items() if k not in ("indicators_done",)},
            "container": e.kind in kinds.container_kinds(),
        }
        if e.delivery == "generate":
            out["how_to_get"] = {
                "tool": "fetch",
                "needs": _needs(e),
                "note": "取数前需要确认（confirm=true）；regions/indicators 见 needs",
            }
        elif e.delivery == "download":
            out["how_to_get"] = {
                "tool": "download",
                "note": "下载前需要确认（confirm=true）；会先告诉你文件格式与可用性",
            }
        else:
            out["how_to_get"] = {
                "tool": None,
                "note": "只有链接：请在浏览器打开 landing_url / 分发入口里的 url",
            }
        if children or e.kind in kinds.container_kinds():
            out["children"] = [
                _brief(c)
                for c in cat.children(
                    resource_id, limit=max(1, min(limit, 200)), offset=max(0, offset)
                )
            ]
        return out
