import asyncio
import time

import pytest

from datafinder.engine.cache import Cache
from datafinder.engine.errors import ShapeError, SourceError
from datafinder.engine.http import Http
from datafinder.engine.orchestrate import run_search
from datafinder.models import Dataset
from datafinder.query.plan import parse
from datafinder.sources.base import Ctx, Source, SourceSpec


class Fake(Source):
    """可编排行为的假来源：返回结果 / 抛错 / 睡眠。"""

    def __init__(self, id, *, items=None, error=None, delay=0.0, supports=True):
        super().__init__(SourceSpec(id=id, title=id, protocol="fake"))
        self.items = (
            items if items is not None else [Dataset(f"{id} result", f"https://{id}.org/1", id)]
        )
        self.error = error
        self.delay = delay
        self._supports = supports
        self.calls = 0

    def supports(self, plan):
        return self._supports

    def cache_query(self, plan):
        return plan.raw

    async def search(self, ctx, plan, limit):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return [Dataset(**{**d.to_dict(), "resources": []}) for d in self.items]


@pytest.fixture()
def ctx():
    return Ctx(Http())


@pytest.fixture()
def cache(tmp_path):
    c = Cache(tmp_path / "c.db")
    yield c
    c.close()


PLAN = parse("population")


def statuses(outcome):
    return {s.id: s.status for s in outcome.statuses}


async def test_one_failure_does_not_break_others(ctx):
    ok = Fake("ok")
    bad = Fake("bad", error=SourceError("down", "连接失败"))
    out = await run_search([ok, bad], ctx, PLAN)
    assert statuses(out) == {"ok": "ok", "bad": "error"}
    assert list(out.per_source) == ["ok"]
    assert not out.complete


async def test_failure_is_never_reported_as_zero_results(ctx):
    out = await run_search([Fake("bad", error=ShapeError("结构变了"))], ctx, PLAN)
    assert out.statuses[0].status == "error"
    assert "结构变了" in out.statuses[0].message
    assert out.per_source == {}  # 不是 {"bad": []}


async def test_unexpected_adapter_exception_is_isolated(ctx):
    out = await run_search([Fake("buggy", error=KeyError("x")), Fake("ok")], ctx, PLAN)
    assert statuses(out)["buggy"] == "error" and statuses(out)["ok"] == "ok"


async def test_per_source_timeout(ctx):
    out = await run_search([Fake("slow", delay=2.0), Fake("ok")], ctx, PLAN, per_source_timeout=0.1)
    assert statuses(out) == {"slow": "timeout", "ok": "ok"}


async def test_global_deadline_marks_not_waited(ctx):
    started = time.monotonic()
    out = await run_search(
        [Fake("slow", delay=5.0), Fake("ok")], ctx, PLAN, deadline=0.2, per_source_timeout=10
    )
    assert time.monotonic() - started < 2
    assert statuses(out) == {"slow": "not_waited", "ok": "ok"}


async def test_quorum_stops_waiting_after_grace(ctx, monkeypatch):
    monkeypatch.setattr("datafinder.engine.orchestrate.QUORUM_GRACE", 0.2)
    fast = [Fake(f"f{i}") for i in range(9)]
    slow = Fake("slow", delay=5.0)
    started = time.monotonic()
    out = await run_search([*fast, slow], ctx, PLAN, deadline=10, per_source_timeout=10)
    assert time.monotonic() - started < 2  # 没有等满 5 秒
    assert statuses(out)["slow"] == "not_waited"
    assert sum(1 for s in out.statuses if s.status == "ok") == 9


async def test_fewer_than_ten_sources_wait_for_all(ctx, monkeypatch):
    monkeypatch.setattr("datafinder.engine.orchestrate.QUORUM_GRACE", 0.05)
    out = await run_search([Fake("a"), Fake("b", delay=0.4)], ctx, PLAN, deadline=5)
    assert statuses(out) == {"a": "ok", "b": "ok"}


async def test_named_sources_wait_full_deadline_despite_quorum(ctx, monkeypatch):
    monkeypatch.setattr("datafinder.engine.orchestrate.QUORUM_GRACE", 0.05)
    fast = [Fake(f"f{i}") for i in range(9)]
    named = Fake("named", delay=0.5)
    out = await run_search([*fast, named], ctx, PLAN, deadline=5, explicit=frozenset({"named"}))
    assert statuses(out)["named"] == "ok"


async def test_unsupported_source_is_skipped_not_failed(ctx):
    out = await run_search([Fake("zh-only", supports=False), Fake("ok")], ctx, PLAN)
    assert statuses(out)["zh-only"] == "skipped"
    assert out.complete  # 跳过不算不完整


async def test_cache_hit_avoids_second_call(ctx, cache):
    src = Fake("ok")
    await run_search([src], ctx, PLAN, cache=cache)
    out = await run_search([src], ctx, PLAN, cache=cache)
    assert src.calls == 1
    assert statuses(out)["ok"] == "cached"
    await run_search([src], ctx, PLAN, cache=cache, refresh=True)
    assert src.calls == 2


async def test_outage_marks_source_resting_and_uses_stale_cache(ctx, cache):
    src = Fake("flaky")
    await run_search([src], ctx, PLAN, cache=cache)  # 先写入缓存
    src.error = SourceError("down", "连接失败")
    out = await run_search([src], ctx, PLAN, cache=cache, refresh=True)
    assert statuses(out)["flaky"] == "stale"  # 失败时降级用过期缓存
    assert out.per_source["flaky"]
    calls = src.calls
    out = await run_search([src], ctx, PLAN, cache=cache, refresh=True)
    assert src.calls == calls  # 休息期内不再请求
    assert statuses(out)["flaky"] == "stale"


async def test_resting_without_cache_reports_resting(ctx, cache):
    cache.mark_down("flaky", "连接失败")
    out = await run_search([Fake("flaky")], ctx, PLAN, cache=cache)
    assert statuses(out)["flaky"] == "resting"


async def test_named_source_ignores_resting(ctx, cache):
    cache.mark_down("flaky", "连接失败")
    src = Fake("flaky")
    out = await run_search([src], ctx, PLAN, cache=cache, explicit=frozenset({"flaky"}))
    assert statuses(out)["flaky"] == "ok" and src.calls == 1


async def test_shape_error_does_not_mark_outage(ctx, cache):
    await run_search([Fake("api", error=ShapeError("结构变了"))], ctx, PLAN, cache=cache)
    assert cache.resting("api") is None  # 接口变了不是"挂了"，不休息


async def test_success_clears_outage_mark(ctx, cache):
    cache.mark_down("api", "x", rest=-1)  # 已过期的标记
    src = Fake("api")
    await run_search([src], ctx, PLAN, cache=cache)
    assert cache.resting("api") is None
