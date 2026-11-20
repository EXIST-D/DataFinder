"""向核心注册本领域的扩展点：来源协议与来源登记、统计术语词表、主题分类词典与规则、站点限速、适配器。"""

from __future__ import annotations

from datafinder.catalog.adapters import register_adapter
from datafinder.catalog.taxonomy import register_taxonomy
from datafinder.finder import MIN_INTERVAL
from datafinder.query.units import register_thesaurus
from datafinder.resources import load_package_yaml
from datafinder.server.routers import register_router
from datafinder.sources.registry import register_protocol

from . import DOMAIN_ID
from .nbs.adapter import NbsAdapter
from .nbs.search import NbsSource
from .yearbook.adapter import YearbookAdapter

PACKAGE = f"datafinder.domains.{DOMAIN_ID}"

register_protocol("nbs", NbsSource)
register_thesaurus(DOMAIN_ID, load_package_yaml(PACKAGE, "thesaurus.zh.yaml"))
register_taxonomy(
    DOMAIN_ID, load_package_yaml(PACKAGE, "taxonomy.yaml"), load_package_yaml(PACKAGE, "topic_rules.yaml")
)
# 国家统计局发布库前面有防火墙，对并发/高频敏感：串行且间隔 ≥0.8 秒
MIN_INTERVAL["data.stats.gov.cn"] = 0.8
register_adapter(NbsAdapter())
register_adapter(YearbookAdapter())


def _web_router():
    from .web import router  # 懒加载：只有启动网页时才需要 FastAPI

    return router


register_router(_web_router)
