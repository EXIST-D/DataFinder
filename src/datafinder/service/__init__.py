"""服务层：MCP、命令行、网页共用的"找数 + 取数"能力。与领域无关——领域通过适配器与词典接入。

模块：
- errors     ServiceError（稳定错误码 + 下一步）与异常翻译
- discover   检索、分面词表、来源列表（search / facets / sources）
- detail     检视一个资源：层级、分发入口、怎么获取（inspect）
- acquire    获取：fetch（需要生成）/ download（可直接下载）/ get（自动分流），默认只预览
- plan       找数方案（基于通用检索）
- index      目录的抓取与旧索引迁移
- export     取数结果的保存、读回与描述统计
- overview   整体状态（status）
- tree       目录树：命中集合按主题/地域/来源展开
- presentation 汇报：给助手的简明文字与统一的 presentation 块
所有函数返回可 JSON 序列化的 dict/list。
"""

from . import index
from .acquire import download, fetch, get
from .detail import inspect
from .discover import facets, search, sources
from .errors import ServiceError, guard, translate
from .export import load_table, table_result
from .overview import status
from .plan import plan_sources
from .tree import tree

__all__ = [
    "ServiceError",
    "download",
    "facets",
    "fetch",
    "get",
    "guard",
    "index",
    "inspect",
    "load_table",
    "plan_sources",
    "search",
    "sources",
    "status",
    "table_result",
    "translate",
    "tree",
]
