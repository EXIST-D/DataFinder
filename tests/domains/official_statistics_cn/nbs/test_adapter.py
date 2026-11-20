from contextlib import asynccontextmanager

import pytest

from datafinder.catalog import Catalog
from datafinder.catalog.adapters import AdapterError, DiscoverOptions, FetchParams
from datafinder.domains.official_statistics_cn.nbs import adapter as nbs_adapter
from datafinder.domains.official_statistics_cn.nbs.adapter import NbsAdapter, parse_codes
from datafinder.engine.errors import SourceError

ROOT = "a" * 32
C1, C2, C3 = "1" * 32, "2" * 32, "3" * 32


def node(id_, name, leaf=False):
    return {"id": id_, "name": name, "is_leaf": leaf, "root_id": ROOT, "sdate": None, "edate": None}


class FakeClient:
    """按分类树结构返回固定数据，并记录"网络请求"次数。"""

    def __init__(self, fail_on: str | None = None):
        self.calls = 0
        self.fail_on = fail_on
        self.tree_data = {
            ("6", ""): [node("root6", "分省年度数据")],
            ("6", "root6"): [node("cat1", "工业"), node("cat2", "人口")],
            ("6", "cat1"): [node(C1, "规模以上工业企业主要经济指标 (2012-2022)", True), node(C2, "工业产品产量", True)],
            ("6", "cat2"): [node(C3, "人口数", True)],
        }
        self.inds = {
            C1: [("a1", "规模以上工业企业单位数 (个)", "个"), ("a2", "规模以上工业企业利润总额 (亿元)", "亿元")],
            C2: [("a3", "原煤产量 (万吨)", "万吨")],
            C3: [("a4", "年末常住人口 (万人)", "万人")],
        }

    async def tree(self, code, pid=""):
        self.calls += 1
        return self.tree_data[(code, pid)]

    async def indicators(self, cid):
        self.calls += 1
        if cid == self.fail_on:
            raise SourceError("rate_limited", "出现访问防护验证页")
        return [{"id": i * 8, "name": n, "unit": u, "mark": None} for i, n, u in self.inds[cid]]


@pytest.fixture
def use_client(monkeypatch):
    def install(client):
        @asynccontextmanager
        async def fake():
            yield client

        monkeypatch.setattr(nbs_adapter, "open_client", fake)
        return client

    return install


@pytest.fixture
def cat(tmp_path):
    c = Catalog.open(tmp_path / "c.db")
    yield c
    c.close()


def titles(cat, kind):
    return sorted(e.title for e in cat.iter_all("nbs") if e.kind == kind)


async def test_discover_builds_full_hierarchy_with_classification(cat, use_client):
    use_client(FakeClient())
    res = await NbsAdapter().discover(cat, DiscoverOptions(params={"db": "6"}))
    assert res.status == "done" and res.added == 3 + 4 + 0  # 3 个数据集 + 4 个指标
    assert titles(cat, "database") == ["分省年度数据"] and titles(cat, "category") == ["人口", "工业"]
    ds = cat.get(f"nbs:dataset:6/{C1}")
    assert ds.parent_id == "nbs:category:6/工业" and ds.extra["indicators_done"] is True and ds.period_start == 2012
    assert {"cnstat.industry.enterprise_performance"} <= {t.topic_id for t in ds.topics}
    assert [c.title for c in cat.children(ds.resource_id)] == ["规模以上工业企业单位数", "规模以上工业企业利润总额"]
    assert cat.conn.execute("SELECT status FROM ingest_runs").fetchone()[0] == "ok"
    assert cat.conn.execute("SELECT COUNT(*) FROM sources WHERE source_id='nbs'").fetchone()[0] == 1


async def test_discover_is_resumable_and_idempotent(cat, use_client):
    use_client(FakeClient(fail_on=C2))
    first = await NbsAdapter().discover(cat, DiscoverOptions(params={"db": "6"}))
    assert first.status == "paused" and "稍后" in first.message
    assert cat.exists(f"nbs:dataset:6/{C1}") and not cat.exists(f"nbs:dataset:6/{C2}")  # 已完成的保存，失败的下次重做
    second = use_client(FakeClient())
    await NbsAdapter().discover(cat, DiscoverOptions(params={"db": "6"}))
    assert cat.exists(f"nbs:dataset:6/{C2}") and cat.exists(f"nbs:dataset:6/{C3}")
    before = cat.stats()["resources"]
    third = use_client(FakeClient())
    res = await NbsAdapter().discover(cat, DiscoverOptions(params={"db": "6"}))
    assert cat.stats()["resources"] == before and res.added == 0  # 全部已完成：不再抓指标
    assert third.calls < second.calls  # 只走了分类树（有缓存时更少），没有再取任何指标清单


async def test_discover_respects_request_budget(cat, use_client):
    c = use_client(FakeClient())
    res = await NbsAdapter().discover(cat, DiscoverOptions(params={"db": "6"}, max_requests=3))
    assert res.status == "limited" and c.calls <= 4 and "重新运行" in res.message


def test_database_code_validation():
    assert parse_codes("3, 6") == ["3", "6"] and len(parse_codes(None)) == 13
    with pytest.raises(AdapterError, match="未知数据库代码"):
        parse_codes("99")


async def test_fetch_requires_a_fetchable_kind_and_indicators(cat, use_client):
    use_client(FakeClient())
    await NbsAdapter().discover(cat, DiscoverOptions(params={"db": "6"}))
    with pytest.raises(AdapterError, match="不能直接取数"):
        await NbsAdapter().fetch(cat, cat.get("nbs:database:6"), FetchParams())
    with pytest.raises(AdapterError, match="指定要取的指标"):
        await NbsAdapter().fetch(cat, cat.get(f"nbs:dataset:6/{C1}"), FetchParams(regions=["河北"]))
