"""国家统计局适配器：目录抓取（discover）、实时取数（fetch）、旧索引迁移（migrate）。

抓取规则（实测教训：发布库前面有防火墙，并发/高频会触发验证页）：串行、间隔 ≥1 秒、遇防护即停并保存进度、
不重试不绕过；只存分类、数据集与指标名称/单位/口径，不存任何数值。已完成的数据集会跳过，所以可以反复续跑。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from datafinder.catalog.adapters import (
    CAP_DISCOVER,
    CAP_FETCH,
    CAP_MIGRATE,
    Adapter,
    AdapterError,
    DiscoverOptions,
    DiscoverResult,
    FetchParams,
    SourceInfo,
)
from datafinder.catalog.entities import Entry
from datafinder.catalog.repository import Catalog
from datafinder.engine.cache import Cache
from datafinder.engine.errors import SourceError
from datafinder.engine.http import Http
from datafinder.finder import MIN_INTERVAL
from datafinder.tables import Table

from .. import DOMAIN_ID
from .client import DATABASES, NbsClient
from .entries import SOURCE_ID, category_entry, database_entry, dataset_entry, indicator_entry
from .fetch import fetch_table

CRAWL_INTERVAL = {**MIN_INTERVAL, "data.stats.gov.cn": 1.0}


@asynccontextmanager
async def open_client() -> AsyncIterator[NbsClient]:
    http, cache = Http(min_interval=CRAWL_INTERVAL), Cache()
    try:
        yield NbsClient(http, cache)
    finally:
        cache.close()
        await http.aclose()


def parse_codes(value: str | None) -> list[str]:
    codes = [c.strip() for c in (value or ",".join(DATABASES)).split(",") if c.strip()]
    bad = [c for c in codes if c not in DATABASES]
    if bad:
        raise AdapterError(f"未知数据库代码：{', '.join(bad)}（可选：{', '.join(DATABASES)}）")
    return codes


class NbsAdapter(Adapter):
    id = "nbs"
    domain_id = DOMAIN_ID
    capabilities = frozenset({CAP_DISCOVER, CAP_FETCH, CAP_MIGRATE})

    def sources(self) -> list[SourceInfo]:
        return [
            SourceInfo(
                SOURCE_ID,
                "国家统计局",
                "国家统计局",
                "https://data.stats.gov.cn",
                "open_with_protection",
            )
        ]

    # ── 目录抓取 ──
    async def discover(self, cat: Catalog, opts: DiscoverOptions) -> DiscoverResult:
        codes = parse_codes(opts.params.get("db"))
        self.register_sources(cat)
        run = cat.start_run(SOURCE_ID, "discover")
        result = DiscoverResult("done")
        async with open_client() as client:
            crawl = _Crawl(cat, client, opts, result)
            try:
                for code in codes:
                    cat.upsert([database_entry(code)])
                    await crawl.walk(code, "", [])
                    if result.status == "limited":
                        break
            except SourceError as exc:
                result.status, result.message = (
                    "paused",
                    f"{exc}。已保存进度，稍后重新运行即可继续。",
                )
            result.requests = client.calls
        cat.finish_run(
            run,
            "ok" if result.status == "done" else result.status,
            seen=result.seen,
            changed=result.added,
            message=result.message or None,
        )
        return result

    # ── 实时取数 ──
    async def fetch(self, cat: Catalog, entry: Entry, params: FetchParams) -> Table:
        if entry.kind not in ("indicator", "dataset"):
            raise AdapterError(
                f"{entry.kind} 不能直接取数：请选一个指标（indicator）或数据集（dataset）"
            )
        ex = entry.extra
        indicators = [ex["indicator_id"]] if entry.kind == "indicator" else params.indicators
        if entry.kind == "dataset" and not indicators:
            raise AdapterError(
                "数据集需要指定要取的指标（indicators）：先 inspect 这个数据集看指标清单"
            )
        async with open_client() as client:
            return await fetch_table(
                client,
                db=ex["db"],
                cid=ex["cid"],
                root_id=ex.get("root_id"),
                indicators=indicators,
                regions=params.regions,
                start=params.start,
                end=params.end,
                periods=params.periods,
            )

    def legacy_present(self) -> bool:
        from datafinder.config import data_dir

        from ..legacy import NBS_LEGACY

        return (data_dir() / NBS_LEGACY).exists()

    def migrate(self, cat: Catalog) -> dict:
        from ..legacy import migrate_nbs

        return migrate_nbs(cat)


class _Crawl:
    def __init__(
        self, cat: Catalog, client: NbsClient, opts: DiscoverOptions, result: DiscoverResult
    ) -> None:
        self.cat, self.client, self.opts, self.result = cat, client, opts, result
        self._start_calls = client.calls
        self._categories: set[str] = set()

    def _budget_left(self) -> bool:
        return (
            self.opts.max_requests is None
            or self.client.calls - self._start_calls < self.opts.max_requests
        )

    def _ensure_categories(self, code: str, names: list[str]) -> None:
        for i in range(1, len(names) + 1):
            e = category_entry(code, names[:i])
            if e.resource_id not in self._categories:
                self._categories.add(e.resource_id)
                self.cat.upsert([e])

    def _done(self, resource_id: str) -> bool:
        row = self.cat.conn.execute(
            "SELECT extra FROM resources WHERE resource_id=?", (resource_id,)
        ).fetchone()
        return bool(row and '"indicators_done": true' in row["extra"])

    async def walk(self, code: str, pid: str, names: list[str]) -> None:
        db_name = DATABASES[code][0]
        for node in await self.client.tree(code, pid):
            if pid == "" and not names and node["name"] == db_name and not node["is_leaf"]:
                await self.walk(code, node["id"], [])  # 每个库只有一个与库同名的根节点：不进路径
                continue
            here = [*names, node["name"]]
            if self.result.status == "limited":
                return
            if node["is_leaf"]:
                await self._leaf(code, here, node)
            else:
                self._ensure_categories(code, here)
                await self.walk(code, node["id"], here)

    async def _leaf(self, code: str, here: list[str], node: dict) -> None:
        ds = dataset_entry(code, here, node["id"], node["root_id"])
        if not self.opts.refresh and self._done(ds.resource_id):
            return
        if not self._budget_left():
            self.result.status, self.result.message = (
                "limited",
                "已达到本次请求上限，重新运行可继续。",
            )
            return
        self._ensure_categories(code, here[:-1])
        indicators = await self.client.indicators(node["id"])
        entries = [indicator_entry(ds, i) for i in indicators]
        ds.extra["indicators_done"] = True  # 指标全部取到后才标记完成：中途失败下次会重做这个数据集
        self.cat.upsert([ds, *entries])
        self.result.seen += 1 + len(entries)
        self.result.added += 1 + len(entries)
        self.opts.say(f"{DATABASES[code][0]} · {' › '.join(here)}（{len(entries)} 个指标）")
