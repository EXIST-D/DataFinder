"""服务层：MCP、命令行（后续可含网页）共用的"找数 + 取数"能力。

模块划分：
- errors     ServiceError
- status     整体状态
- nbs        国家统计局：浏览 / 找指标 / 取数
- export     取数结果保存与读回
- discovery  门户浏览、数据集检索
- plan       找数方案
所有函数返回可 JSON 序列化的 dict/list（或 Table）。
"""

from .discovery import datasets_search, portals_list
from .errors import ServiceError
from .export import load_table, table_result
from .nbs import (
    nbs_browse,
    nbs_databases,
    nbs_fetch,
    nbs_find,
    nbs_indicators,
    nbs_search_national,
    resolve_regions,
)
from .plan import plan_sources
from .status import status

__all__ = [
    "ServiceError",
    "datasets_search",
    "load_table",
    "nbs_browse",
    "nbs_databases",
    "nbs_fetch",
    "nbs_find",
    "nbs_indicators",
    "nbs_search_national",
    "plan_sources",
    "portals_list",
    "resolve_regions",
    "status",
    "table_result",
]
