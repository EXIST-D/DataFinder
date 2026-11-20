"""本领域的分类：把一个资源的"顶层分类/路径/标题"变成主题与地域标签。

国家统计局与各类年鉴共用同一套规则（data/topic_rules.yaml），所以放在领域包根目录。
"""

from __future__ import annotations

from datafinder.catalog import taxonomy
from datafinder.catalog.entities import RegionLink, TopicLink
from datafinder.catalog.rules import Target

from . import DOMAIN_ID


def topics_for(target: Target) -> list[TopicLink]:
    return [TopicLink(t, "rule") for t in taxonomy.rules(DOMAIN_ID).topics(target)]


def regions_for(target: Target, base: list[RegionLink]) -> list[RegionLink]:
    """来源结构决定的地域（base）+ 标题/章名推断出的补充（永远是 inferred，不冒充已验证）。

    规则没指定具体区域（如"各市"）时，是"在 base 的主区域内按该粒度报告"。
    """
    out = list(base)
    primary = base[0].region_id if base else None
    for tag in taxonomy.rules(DOMAIN_ID).regions(target):
        region = tag.region or primary
        if region is None:
            continue
        link = RegionLink(region, tag.relation, tag.granularity, tag.scope_mode, tag.evidence_status)
        if link not in out and not any(
            o.region_id == link.region_id and o.relation == link.relation and o.evidence_status == "structure"
            and (o.granularity == link.granularity or link.granularity is None)
            for o in out
        ):
            out.append(link)
    return out
