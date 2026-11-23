"""向核心注册本领域的扩展点：来源协议与来源登记、统计术语词表、主题分类词典与规则、站点限速、适配器。"""

from __future__ import annotations

from datafinder.catalog.adapters import register_adapter
from datafinder.catalog.glossary import register_glossary
from datafinder.catalog.portal_links import register_portal_links
from datafinder.catalog.scout import register_probe
from datafinder.catalog.taxonomy import register_taxonomy
from datafinder.catalog.tree import register_domain
from datafinder.finder import MIN_INTERVAL
from datafinder.query.units import register_thesaurus
from datafinder.resources import load_package_yaml
from datafinder.server.routers import register_router
from datafinder.sources.registry import register_protocol

from . import DOMAIN_ID, admin, glossary_load
from .hmt.hk import HkAdapter
from .hmt.tw import TwAdapter
from .nbs.adapter import NbsAdapter
from .nbs.search import NbsSource
from .province.hebei import HebeiQueryAdapter
from .scout import probe_report
from .sites.adapter import build as build_site_adapter
from .yearbook.adapter import YearbookAdapter

PACKAGE = f"datafinder.domains.{DOMAIN_ID}"

register_domain(DOMAIN_ID, "中国官方统计")
register_portal_links(DOMAIN_ID, load_package_yaml(PACKAGE, "portal_source_links.yaml")["links"])
register_protocol("nbs", NbsSource)
_GLOSSARY, _GLOSSARY_ALIASES = glossary_load.load()
register_glossary(DOMAIN_ID, _GLOSSARY)
# 检索词表：手写词表优先；词典里官方原文写明的缩写/又称（如 GDP、粗出生率）作补充
register_thesaurus(
    DOMAIN_ID, {**_GLOSSARY_ALIASES, **load_package_yaml(PACKAGE, "thesaurus.zh.yaml")}
)
register_taxonomy(
    DOMAIN_ID,
    load_package_yaml(PACKAGE, "taxonomy.yaml"),
    load_package_yaml(PACKAGE, "topic_rules.yaml"),
)
# 国家统计局发布库前面有防火墙，对并发/高频敏感：串行且间隔 ≥0.8 秒
MIN_INTERVAL["data.stats.gov.cn"] = 0.8
register_adapter(NbsAdapter())
register_adapter(YearbookAdapter())
register_adapter(HebeiQueryAdapter())
register_probe(probe_report)
register_adapter(build_site_adapter())
register_adapter(HkAdapter())
register_adapter(TwAdapter())
admin.register()  # 地级、县级行政区划进入地域分面


def _web_router():
    from .web import router  # 懒加载：只有启动网页时才需要 FastAPI

    return router


register_router(_web_router)
