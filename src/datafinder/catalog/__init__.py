"""catalog：统一资源目录。

- kinds / regions / taxonomy / rules   三个分面的词典与规则机制（数据由领域包登记）
- entities / schema / repository       实体、表结构、读写
- query / search                       检索请求、SQL 条件、两阶段检索与分面计数

目录只存元数据与链接；数值实时取或下载到用户本机。
"""

from .entities import Distribution, Entry, RegionLink, TopicLink, make_id
from .query import InvalidFacet, SearchRequest
from .repository import Catalog
from .search import SearchResponse, facet_counts, search

__all__ = [
    "Catalog",
    "Distribution",
    "Entry",
    "InvalidFacet",
    "RegionLink",
    "SearchRequest",
    "SearchResponse",
    "TopicLink",
    "facet_counts",
    "make_id",
    "search",
]
