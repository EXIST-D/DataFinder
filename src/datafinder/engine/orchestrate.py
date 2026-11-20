"""并发调度：每个来源一个任务，隔离失败，设截止时间与"法定人数"。

移植自 Dataseek 的已验证算法（`search.rs`、ADR 0019）：
- 单源超时、总截止时间；
- **法定人数**：已应答来源达到"未失败来源"的 90% 后，只再等 1 秒，迟到的丢弃并标注"未等待"；
  失败不计入分母；来源不足 10 个时等满（"九个及以下等全部"）；用户点名的来源等满总截止；
- 来源出现不可达/超时/限流/5xx：写故障标记，10 分钟内默认不再请求；
- 来源失败时降级使用过期缓存；
- 失败只记在该来源的状态里，**绝不当成"0 条结果"**。
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Literal

from ..models import Dataset
from ..query.plan import QueryPlan
from ..sources.base import Ctx, Source
from .cache import Cache, make_key
from .errors import SourceError

Status = Literal["ok", "cached", "stale", "skipped", "resting", "error", "timeout", "not_waited"]

QUORUM_SHARE = 0.9
QUORUM_GRACE = 1.0
QUORUM_MIN_SOURCES = 10


@dataclass
class SourceStatus:
    id: str
    title: str
    status: Status
    elapsed_ms: int = 0
    count: int = 0
    message: str | None = None

    @property
    def answered(self) -> bool:
        return self.status in ("ok", "cached", "stale")


@dataclass
class Outcome:
    per_source: dict[str, list[Dataset]] = field(default_factory=dict)
    statuses: list[SourceStatus] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """所有应当回答的来源都回答了（跳过的不算）。"""
        return all(s.answered or s.status == "skipped" for s in self.statuses)


async def _one(
    src: Source,
    ctx: Ctx,
    plan: QueryPlan,
    limit: int,
    cache: Cache | None,
    refresh: bool,
    timeout: float,
    explicit: bool,
) -> tuple[list[Dataset], SourceStatus]:
    started = time.monotonic()

    def status(kind: Status, items: list[Dataset], message: str | None = None) -> SourceStatus:
        return SourceStatus(
            src.id, src.title, kind, int((time.monotonic() - started) * 1000), len(items), message
        )

    if not src.supports(plan):
        return [], status("skipped", [], "不适用于这个查询")
    key = make_key(src.id, src.cache_query(plan), limit)

    if cache and not refresh:
        hit = cache.get(key)
        if hit:
            return hit[0], status("cached", hit[0], f"缓存（{int(hit[1] // 60)} 分钟前）")
    if cache and not explicit:
        reason = cache.resting(src.id)
        if reason:
            stale = cache.get(key, max_age=None)
            if stale:
                return stale[0], status("stale", stale[0], f"来源休息中（{reason}），用了过期缓存")
            return [], status("resting", [], f"最近故障，休息中：{reason}")

    try:
        items = await asyncio.wait_for(src.search(ctx, plan, limit), timeout)
    except TimeoutError:
        err = SourceError("timeout", f"超过 {timeout:.0f} 秒未响应")
    except SourceError as exc:
        err = exc
    except Exception as exc:  # 适配器自身的意外异常：当作"响应结构不符"，不休息
        err = SourceError("shape", f"适配器内部错误：{type(exc).__name__}: {exc}")
    else:
        if cache:
            cache.clear_down(src.id)
            cache.put(key, src.id, items)
        return items, status("ok", items)

    if cache and err.is_outage:
        cache.mark_down(src.id, str(err))
        stale = cache.get(key, max_age=None)
        if stale:
            return stale[0], status("stale", stale[0], f"{err}；用了过期缓存")
    kind: Status = "timeout" if err.kind == "timeout" else "error"
    return [], status(kind, [], str(err))


async def run_search(
    sources: list[Source],
    ctx: Ctx,
    plan: QueryPlan,
    *,
    limit: int = 20,
    deadline: float = 20.0,
    per_source_timeout: float = 15.0,
    cache: Cache | None = None,
    refresh: bool = False,
    explicit: frozenset[str] = frozenset(),
) -> Outcome:
    started = time.monotonic()
    stop_at = started + deadline
    n = len(sources)
    tasks: dict[asyncio.Task, Source] = {
        asyncio.create_task(
            _one(s, ctx, plan, limit, cache, refresh, per_source_timeout, s.id in explicit)
        ): s
        for s in sources
    }
    results: dict[str, tuple[list[Dataset], SourceStatus]] = {}
    pending = set(tasks)
    answered = failed = 0
    quorum_applied = False

    while pending:
        timeout = max(0.0, stop_at - time.monotonic())
        done, pending = await asyncio.wait(
            pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            break
        for t in done:
            items, st = t.result()
            results[tasks[t].id] = (items, st)
            if st.answered:
                answered += 1
            elif st.status in ("error", "timeout", "resting"):
                failed += 1
        reachable = n - failed
        if (
            not quorum_applied
            and n >= QUORUM_MIN_SOURCES
            and not (explicit & {tasks[t].id for t in pending})
            and answered > 0
            and answered >= math.ceil(reachable * QUORUM_SHARE)
        ):
            stop_at = min(stop_at, time.monotonic() + QUORUM_GRACE)
            quorum_applied = True

    for t in pending:
        t.cancel()
        src = tasks[t]
        results[src.id] = (
            [],
            SourceStatus(
                src.id,
                src.title,
                "not_waited",
                int((time.monotonic() - started) * 1000),
                0,
                "没等它：已有足够来源回答或到了截止时间",
            ),
        )
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    outcome = Outcome()
    for src in sources:  # 按注册顺序输出，保证去重时的来源优先级稳定
        items, st = results[src.id]
        outcome.statuses.append(st)
        if st.answered:
            outcome.per_source[src.id] = items
    return outcome
