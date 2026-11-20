"""适配器协议：领域包里"懂得一类来源"的代码，按能力声明接入统一目录。

核心只认这个协议；国家统计局、统计年鉴等具体适配器在 `domains/<id>/` 里实现并在 plugin 里登记。
能力是可选的，没有的能力返回 `unsupported`（不假装成功）：
- discover   把来源的目录抓进统一目录（用户主动触发，串行限速，可续跑）
- fetch      对一个资源实时取数 → 整洁表格（获取方式 generate）
- migrate    一次性把旧版本地索引迁入统一目录（迁移完成后删除）
文件直链的下载是通用能力（见 service.fetch），不需要适配器实现。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..tables import Table
from .entities import Entry
from .repository import Catalog

CAP_DISCOVER, CAP_FETCH, CAP_MIGRATE = "discover", "fetch", "migrate"


class Unsupported(RuntimeError):
    """适配器没有这项能力。"""


class AdapterError(ValueError):
    """适配器给出的、写给用户（或 AI 助手）看的错误：请求参数不对、对象不存在等，消息里要说明怎么改。"""


@dataclass
class SourceInfo:
    source_id: str
    name: str
    publisher: str | None = None
    base_url: str | None = None
    access_policy: str = "unknown"


@dataclass
class DiscoverOptions:
    params: dict[str, str] = field(default_factory=dict)  # 适配器私有参数（如库代码、年份范围）
    refresh: bool = False
    max_requests: int | None = None
    progress: Callable[[str], None] | None = None

    def say(self, message: str) -> None:
        if self.progress:
            self.progress(message)


@dataclass
class DiscoverResult:
    status: str  # done | paused | limited
    seen: int = 0
    added: int = 0
    requests: int = 0
    message: str = ""
    stopped: list[str] = field(default_factory=list)  # 被站点拒绝/限流而放弃的部分及原因


@dataclass
class FetchParams:
    regions: list[str] = field(default_factory=list)  # 地区名/代码（"全部"=全部）
    indicators: list[str] = field(default_factory=list)  # 数据集级取数时选哪些指标（名称或 id）
    start: str | None = None
    end: str | None = None
    periods: int = 8  # 不指定起止时取最近多少期


class Adapter:
    id: str = ""
    domain_id: str = ""
    capabilities: frozenset[str] = frozenset()

    def sources(self) -> list[SourceInfo]:
        raise NotImplementedError

    async def discover(self, cat: Catalog, opts: DiscoverOptions) -> DiscoverResult:
        raise Unsupported(f"{self.id} 不支持 discover")

    async def fetch(self, cat: Catalog, entry: Entry, params: FetchParams) -> Table:
        raise Unsupported(f"{self.id} 不支持 fetch（该资源没有实时取数能力）")

    def migrate(self, cat: Catalog) -> dict[str, Any]:
        raise Unsupported(f"{self.id} 没有需要迁移的旧索引")

    def legacy_present(self) -> bool:
        """本机是否还有等待迁移的旧版索引（用于提示用户运行迁移）。"""
        return False

    def register_sources(self, cat: Catalog) -> None:
        for s in self.sources():
            cat.upsert_source(
                s.source_id, domain_id=self.domain_id, adapter_id=self.id, name=s.name, publisher=s.publisher,
                base_url=s.base_url, capabilities=self.capabilities, access_policy=s.access_policy,
            )


_ADAPTERS: dict[str, Adapter] = {}


def register_adapter(adapter: Adapter) -> None:
    if not adapter.id or not adapter.domain_id:
        raise ValueError("适配器必须有 id 和 domain_id")
    _ADAPTERS[adapter.id] = adapter


def adapters() -> dict[str, Adapter]:
    from .. import domains

    domains.load_all()
    return dict(_ADAPTERS)


def get_adapter(adapter_id: str) -> Adapter:
    found = adapters().get(adapter_id)
    if found is None:
        raise Unsupported(f"没有名为 {adapter_id!r} 的适配器；可用：{', '.join(sorted(adapters()))}")
    return found


def adapter_for(cat: Catalog, source_id: str) -> Adapter:
    """某个来源由哪个适配器负责（登记在 sources 表里）。"""
    row = cat.conn.execute("SELECT adapter_id FROM sources WHERE source_id=?", (source_id,)).fetchone()
    if row is None:
        raise Unsupported(f"目录里没有来源 {source_id!r}")
    return get_adapter(row["adapter_id"])
