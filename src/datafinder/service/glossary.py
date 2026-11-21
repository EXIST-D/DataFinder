"""术语词典：查词、看定义与出处、找相关资源（只读、本地）。

定义是官方页面原文的逐字摘录（verification_status = official_sourced）或人工审校过的；检索只使用已确认的别名，
related 关系只用于"也可以看看"，不会悄悄替代原词。
"""

from __future__ import annotations

import re
from typing import Any

from datafinder.catalog import glossary, taxonomy
from datafinder.catalog.glossary import Term

from .common import open_catalog
from .errors import ServiceError, guard

STATUS_ZH = {"draft": "草稿（未核对来源）", "official_sourced": "官方原文", "reviewed": "已审校"}
RELATION_ZH = {
    "alias": "同一概念",
    "related": "相关",
    "broader": "上位概念",
    "narrower": "下位概念",
    "not_equivalent": "容易混淆，不能互相替代",
}
BRIEF_CHARS = 120
_TRAILING_PAREN = re.compile(r"\s*[（(][^（）()]*[）)]\s*$")


def _first_sentence(text: str, limit: int = BRIEF_CHARS) -> str:
    one = " ".join(text.split())
    cut = one[:limit]
    end = max(cut.rfind("。"), cut.rfind("；"))
    return cut[: end + 1] if end >= 20 else (cut + "…" if len(one) > limit else cut)


def _topic_names(t: Term) -> list[dict[str, str]]:
    return [{"id": i, "name": x.name_zh} for i in t.topic_ids if (x := taxonomy.get(i)) is not None]


def brief(t: Term, *, chars: int = BRIEF_CHARS) -> dict[str, Any]:
    """列表/嵌入用的简要形式。"""
    return {
        "term_id": t.term_id,
        "name_zh": t.name_zh,
        "aliases": list(t.aliases),
        "topics": _topic_names(t),
        "definition": _first_sentence(t.definition, chars),
        "verification_status": t.verification_status,
        "status_zh": STATUS_ZH[t.verification_status],
        "source_name": t.source_name,
        "source_url": t.source_url,
    }


def _topic_facets(q: str, domain: str | None, status: str | None) -> list[dict[str, Any]]:
    """各一级主题下的术语数（不应用主题筛选，兄弟主题才能保持可见）。"""
    _, every = glossary.search(q, domain_id=domain, status=status, limit=10**6)
    counts: dict[str, int] = {}
    for t in every:
        for top in {(taxonomy.ancestors(i) or [i])[-1] for i in t.topic_ids}:
            counts[top] = counts.get(top, 0) + 1
    return sorted(
        (
            {"id": i, "name": x.name_zh, "count": n}
            for i, n in counts.items()
            if (x := taxonomy.get(i))
        ),
        key=lambda x: (-x["count"], x["name"]),
    )


def list_terms(
    q: str = "",
    *,
    topic: str | None = None,
    domain: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    with guard():
        topic_id = None
        if topic:
            topic_id = taxonomy.resolve(topic)
            if not topic_id:
                raise ServiceError(
                    f"未知主题 {topic!r}",
                    code="INVALID_FACET_ID",
                    next_action="用 facets 查看合法的主题",
                )
        if status and status not in STATUS_ZH:
            raise ServiceError(f"status 只能是 {' / '.join(STATUS_ZH)}", code="INVALID_ARGUMENT")
        total, items = glossary.search(
            q,
            topic_id=topic_id,
            domain_id=domain,
            status=status,
            limit=max(1, min(limit, 50)),
            offset=max(0, offset),
        )
        return {
            "total": total,
            "offset": max(0, offset),
            "items": [brief(t) for t in items],
            "topics": _topic_facets(q, domain, status),
            "statuses": STATUS_ZH,
            "note": "定义来自官方页面原文，逐字摘录并注明出处；术语之间的关系只是提示，不能当作同义词替换。",
        }


def _source_resource(url: str | None) -> str | None:
    """出处页面如果也在目录里（reference 类资源），给出它的 resource_id，方便用 inspect 查看。"""
    if not url:
        return None
    with open_catalog(require_data=False) as cat:
        row = cat.conn.execute(
            "SELECT resource_id FROM resources WHERE landing_url = ? LIMIT 1", (url,)
        ).fetchone()
    return row["resource_id"] if row else None


def detail(term_id: str) -> dict[str, Any]:
    with guard():
        t = glossary.get(term_id)
        if t is None:
            raise ServiceError(
                f"词典里没有术语 {term_id!r}",
                code="NOT_FOUND",
                next_action="用术语列表（/api/glossary）里的 term_id",
            )
        rels = []
        for r in t.relations:
            target = glossary.get(r.target_id)
            if target:
                rels.append(
                    {
                        "type": r.type,
                        "type_zh": RELATION_ZH[r.type],
                        "term_id": target.term_id,
                        "name_zh": target.name_zh,
                        "note": r.note,
                    }
                )
        same_name = [
            {"term_id": o.term_id, "source_name": o.source_name}
            for o in glossary.find_by_name(t.name_zh)
            if o.term_id != t.term_id
        ]
        out = {
            **brief(t),
            "source_resource_id": _source_resource(t.source_url),
            "definition": t.definition,
            "name_en": t.name_en,
            "scope_note": t.scope_note,
            "unit_note": t.unit_note,
            "note": t.note,
            "retrieved_at": t.retrieved_at,
            "relations": sorted(
                rels, key=lambda x: (x["type"] == "not_equivalent", x["type"], x["name_zh"])
            ),
            "same_name_terms": same_name,
            "find_data": {"query": t.name_zh, "topic_ids": list(t.topic_ids[:1])},
        }
        if t.verification_status == "draft":
            out["warning"] = "草稿：定义尚未核对来源，不要当作官方解释引用。"
        return out


async def resources(term_id: str, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """用术语名检索相关资源（复用目录检索）。"""
    from .discover import search

    t = glossary.get(term_id)
    if t is None:
        raise ServiceError(f"词典里没有术语 {term_id!r}", code="NOT_FOUND")
    return await search(t.name_zh, limit=limit, offset=offset, facets=False)


def for_text(text: str, *, limit: int = 6) -> list[dict[str, Any]]:
    """文本里出现的术语（找数方案用：给出官方口径说明）。"""
    return [brief(t, chars=100) for t in glossary.match_text(text, limit=limit)]


def for_title(title: str, *, limit: int = 2) -> list[dict[str, Any]]:
    """资源标题（去掉末尾单位括号）与术语名完全相同的术语（资源详情里的"术语说明"）。"""
    key = _TRAILING_PAREN.sub("", title).strip()
    return [brief(t, chars=200) for t in glossary.find_by_name(key)[:limit]] if key else []
