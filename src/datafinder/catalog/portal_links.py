"""门户—来源的人工认定（例外清单）：领域包登记，核心只存取。

默认的关联是"网址规范化后完全一致"自动算出来的；只有自动算不出、但人确认过的关系才写在这里
（如：省年鉴挂在省统计局网站上）。门户在门户库、来源在目录库，两边不建外键，用规范化网址当稳定键。
"""

from __future__ import annotations

from typing import Any

RELATIONS = ("same_site", "hosted_on", "section", "related")  # related 只作提示，不计入接入等级

_LINKS: dict[str, list[dict[str, Any]]] = {}


def register_portal_links(domain_id: str, links: list[dict[str, Any]]) -> None:
    for link in links:
        if link.get("relation") not in RELATIONS:
            raise ValueError(f"门户关联的 relation 只能是 {RELATIONS}：{link}")
        if not link.get("portal_key") or not link.get("source_id"):
            raise ValueError(f"门户关联必须有 portal_key 与 source_id：{link}")
    _LINKS[domain_id] = list(links)


def overrides() -> list[dict[str, Any]]:
    from .. import domains

    domains.load_all()
    return [link for links in _LINKS.values() for link in links]
