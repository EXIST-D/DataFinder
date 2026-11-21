"""官方网站栏目适配器（统计局、部委等）：通用抓取逻辑在核心 `catalog.sites`，这里只接入本领域的主题/地域规则与配置。"""

from __future__ import annotations

from datafinder.catalog.entities import RegionLink, TopicLink
from datafinder.catalog.rules import Target
from datafinder.catalog.sites import SiteAdapter, load_spec
from datafinder.resources import load_package_yaml

from .. import DOMAIN_ID
from ..classify import regions_for, topics_for


class StatSiteAdapter(SiteAdapter):
    def classify(
        self, target: Target, base: list[RegionLink]
    ) -> tuple[list[TopicLink], list[RegionLink]]:
        return topics_for(target), regions_for(target, base)


def build() -> StatSiteAdapter:
    spec = load_spec(load_package_yaml(f"datafinder.domains.{DOMAIN_ID}", "sites.yaml"))
    return StatSiteAdapter(spec, DOMAIN_ID)
