"""主题分面：主题树（由各领域包登记）与分类规则。

核心只提供机制：登记、查询、父子关系；具体的主题词典和规则在领域包的 data/ 里。
主题 id 稳定、名称可改；一个资源可属于多个主题；`global_topics` 把领域主题映射到平台级的粗粒度行业标签（industries.yaml）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .. import domains
from .rules import RuleSet


@dataclass(frozen=True)
class Topic:
    id: str
    domain: str
    parent_id: str | None
    name_zh: str
    name_en: str | None
    level: int
    aliases: tuple[str, ...] = ()
    global_topics: tuple[str, ...] = ()
    reserved: bool = False  # 预留主题（暂无数据），默认不出现在分面里


_TOPICS: dict[str, dict[str, Topic]] = {}  # 领域 id -> {主题 id: Topic}
_RULES: dict[str, RuleSet] = {}
_VERSIONS: dict[str, str] = {}


def register_taxonomy(domain_id: str, taxonomy: dict, rules: dict | None = None) -> None:
    """领域包登记自己的主题树与分类规则；主题 id 必须全局唯一、父节点必须存在。"""
    topics: dict[str, Topic] = {}
    for t in taxonomy["topics"]:
        parent = t.get("parent_id")
        topic = Topic(
            id=t["id"], domain=domain_id, parent_id=parent, name_zh=t["name_zh"], name_en=t.get("name_en"),
            level=2 if parent else 1, aliases=tuple(t.get("aliases", ())),
            global_topics=tuple(t.get("global_topics", ())), reserved=bool(t.get("reserved", False)),
        )
        topics[topic.id] = topic
    for t in topics.values():
        if t.parent_id and t.parent_id not in topics:
            raise ValueError(f"主题 {t.id} 的父主题 {t.parent_id} 不存在")
    for tid, t in list(topics.items()):  # 二级主题没写 global_topics 时继承父主题的
        if t.parent_id and not t.global_topics:
            topics[tid] = replace(t, global_topics=topics[t.parent_id].global_topics)
    dup = (set(topics) & {i for d, tt in _TOPICS.items() if d != domain_id for i in tt})
    if dup:
        raise ValueError(f"主题 id 与其他领域重复：{sorted(dup)}")
    _TOPICS[domain_id] = topics
    _VERSIONS[domain_id] = str(taxonomy.get("version", ""))
    _RULES[domain_id] = RuleSet.from_raw(rules or {})


def topics() -> dict[str, Topic]:
    domains.load_all()
    return {i: t for tt in _TOPICS.values() for i, t in tt.items()}


def rules(domain_id: str) -> RuleSet:
    domains.load_all()
    return _RULES.get(domain_id, RuleSet())


def version(domain_id: str) -> str:
    domains.load_all()
    return _VERSIONS.get(domain_id, "")


def get(topic_id: str) -> Topic | None:
    return topics().get(topic_id)


def children(topic_id: str) -> list[Topic]:
    return [t for t in topics().values() if t.parent_id == topic_id]


def descendants(topic_id: str) -> list[str]:
    kids = children(topic_id)
    return [k.id for k in kids] + [d for k in kids for d in descendants(k.id)]


def ancestors(topic_id: str) -> list[str]:
    out: list[str] = []
    t = get(topic_id)
    while t and t.parent_id:
        out.append(t.parent_id)
        t = get(t.parent_id)
    return out


def resolve(value: str) -> str | None:
    """主题 id / 中文名 / 别名 → 主题 id；认不出返回 None。"""
    value = value.strip()
    all_topics = topics()
    if value in all_topics:
        return value
    for t in all_topics.values():
        if value == t.name_zh or value in t.aliases:
            return t.id
    return None
