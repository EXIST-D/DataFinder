"""术语词典：术语的定义、别名、关系与出处（由领域包登记，核心只存取与检索）。

与检索词表（thesaurus）的分工：词表回答"用户这样说，统计里叫什么"，是检索扩展；
词典回答"这个词到底是什么意思、和什么容易混淆、定义出自哪里"，是可引用的说明。
两者不混用——词典里只有已确认的别名可以回流检索，`related` 永远不会被当作同义词。

字段与关系的命名对齐 SKOS（prefLabel/altLabel/broader/narrower/related），方便以后导出；存储就是普通数据对象。
验证状态：draft 草稿（未核对来源）→ official_sourced 官方原文（逐字摘自官方页面，有出处）→ reviewed 人工审校过。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import domains
from ..query.tokenize import normalize
from . import taxonomy

RELATION_TYPES = ("alias", "related", "broader", "narrower", "not_equivalent")
STATUSES = ("draft", "official_sourced", "reviewed")
SOURCED = ("official_sourced", "reviewed")  # 有官方出处的状态
_INVERSE = {"broader": "narrower", "narrower": "broader"}
_SYMMETRIC = ("alias", "related", "not_equivalent")


@dataclass(frozen=True)
class Relation:
    type: str
    target_id: str
    note: str | None = None


@dataclass(frozen=True)
class Term:
    term_id: str
    domain_id: str
    name_zh: str
    definition: str
    topic_ids: tuple[str, ...]
    verification_status: str
    name_en: str | None = None
    aliases: tuple[str, ...] = ()
    scope_note: str | None = None
    unit_note: str | None = None
    relations: tuple[Relation, ...] = ()
    source_name: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None
    note: str | None = None


_TERMS: dict[str, dict[str, Term]] = {}  # 领域 id -> {term_id: Term}


def build_term(domain_id: str, raw: dict[str, Any]) -> Term:
    """校验并构造术语；不合格直接报错（词典里不放来历不明的条目）。"""
    tid = raw.get("term_id")
    if not tid or not raw.get("name_zh") or not str(raw.get("definition", "")).strip():
        raise ValueError(f"术语必须有 term_id、name_zh、definition：{raw.get('name_zh') or raw}")
    status = raw.get("verification_status", "draft")
    if status not in STATUSES:
        raise ValueError(f"{tid}：verification_status 只能是 {STATUSES}，收到 {status!r}")
    if status in SOURCED and not raw.get("source_url"):
        raise ValueError(f"{tid}：状态 {status} 必须有 source_url（官方出处）")
    rels = []
    for r in raw.get("relations", []):
        if r["type"] not in RELATION_TYPES:
            raise ValueError(f"{tid}：关系类型只能是 {RELATION_TYPES}，收到 {r['type']!r}")
        rels.append(Relation(r["type"], r["target_id"], r.get("note")))
    return Term(
        term_id=tid,
        domain_id=domain_id,
        name_zh=raw["name_zh"],
        definition=str(raw["definition"]).strip(),
        topic_ids=tuple(raw.get("topic_ids", ())),
        verification_status=status,
        name_en=raw.get("name_en"),
        aliases=tuple(dict.fromkeys(raw.get("aliases", ()))),
        scope_note=raw.get("scope_note"),
        unit_note=raw.get("unit_note"),
        relations=tuple(rels),
        source_name=raw.get("source_name"),
        source_url=raw.get("source_url"),
        retrieved_at=raw.get("retrieved_at"),
        note=raw.get("note"),
    )


def register_glossary(domain_id: str, terms: list[dict[str, Any]]) -> None:
    """领域包登记术语（整批替换该领域的旧数据）。关系的对端必须存在；对称/反向关系自动补全。"""
    built = {}
    for raw in terms:
        t = build_term(domain_id, raw)
        if t.term_id in built or any(t.term_id in d for k, d in _TERMS.items() if k != domain_id):
            raise ValueError(f"术语 id 重复：{t.term_id}")
        built[t.term_id] = t
    for t in built.values():
        for r in t.relations:
            if r.target_id not in built:
                raise ValueError(f"{t.term_id} 的关系指向不存在的术语 {r.target_id}")
            if r.target_id == t.term_id:
                raise ValueError(f"{t.term_id} 的关系指向自己")
    _TERMS[domain_id] = _with_inverses(built)


def _with_inverses(terms: dict[str, Term]) -> dict[str, Term]:
    """关系成对出现：A narrower B ⇒ B broader A；A related/not_equivalent/alias B ⇒ B 也有。"""
    extra: dict[str, list[Relation]] = {}
    for t in terms.values():
        for r in t.relations:
            back = _INVERSE.get(r.type, r.type if r.type in _SYMMETRIC else None)
            if back and not any(
                x.type == back and x.target_id == t.term_id for x in terms[r.target_id].relations
            ):
                extra.setdefault(r.target_id, []).append(Relation(back, t.term_id, r.note))
    out = {}
    for tid, t in terms.items():
        add = [
            x
            for i, x in enumerate(extra.get(tid, []))
            if not any(y.type == x.type and y.target_id == x.target_id for y in extra[tid][:i])
        ]
        out[tid] = Term(**{**t.__dict__, "relations": (*t.relations, *add)}) if add else t
    return out


def terms(domain_id: str | None = None) -> dict[str, Term]:
    domains.load_all()
    if domain_id:
        return dict(_TERMS.get(domain_id, {}))
    return {i: t for d in _TERMS.values() for i, t in d.items()}


def get(term_id: str) -> Term | None:
    return terms().get(term_id)


def _names(t: Term) -> list[str]:
    return [normalize(n) for n in (t.name_zh, *t.aliases)]


def find_by_name(name: str) -> list[Term]:
    """名称或别名（规范化后）与 name 完全相同的术语；同名可能有多条（各页面各一条）。"""
    key = normalize(name.strip())
    return [t for t in terms().values() if key and key in _names(t)]


def search(
    q: str = "",
    *,
    topic_id: str | None = None,
    domain_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[int, list[Term]]:
    """按名称/别名/定义检索。名称完全相同 > 名称前缀 > 名称包含 > 定义包含；无关键词时按主题浏览。"""
    topic_ids: set[str] | None = None
    if topic_id:
        topic_ids = {topic_id, *taxonomy.descendants(topic_id)}
    key = normalize(q.strip())
    scored: list[tuple[int, str, Term]] = []
    for t in terms(domain_id).values():
        if topic_ids is not None and not topic_ids & set(t.topic_ids):
            continue
        if status and t.verification_status != status:
            continue
        if key:
            names = _names(t)
            if key in names:
                rank = 0
            elif any(n.startswith(key) for n in names):
                rank = 1
            elif any(key in n for n in names):
                rank = 2
            elif key in normalize(t.definition):
                rank = 3
            else:
                continue
        else:
            rank = 0
        scored.append((rank, t.name_zh, t))
    scored.sort(key=lambda x: (x[0], len(x[1]), x[1], x[2].term_id))
    return len(scored), [t for _, _, t in scored[offset : offset + limit]]


def match_text(text: str, *, limit: int = 6, domain_id: str | None = None) -> list[Term]:
    """文本里出现的术语（名称/别名整词包含，长的优先、不重叠、同名只取第一条）。用于找数方案给出口径说明。"""
    norm = normalize(text)
    hits: list[tuple[int, int, Term]] = []
    for t in terms(domain_id).values():
        for n in _names(t):
            if (
                len(n) >= 3 and (i := norm.find(n)) >= 0
            ):  # 两个字的名称太泛（工业、存款），不用于文本匹配
                hits.append((len(n), i, t))
                break
    hits.sort(key=lambda x: (-x[0], x[1], x[2].term_id))
    out, taken, seen = [], [], set()
    for ln, i, t in hits:
        if t.name_zh in seen or any(i < b and a < i + ln for a, b in taken):
            continue
        seen.add(t.name_zh)
        taken.append((i, i + ln))
        out.append(t)
        if len(out) >= limit:
            break
    return out


__all__ = [
    "RELATION_TYPES",
    "STATUSES",
    "Relation",
    "Term",
    "build_term",
    "find_by_name",
    "get",
    "match_text",
    "register_glossary",
    "search",
    "terms",
]
