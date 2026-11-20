import pytest

from datafinder.domains.official_statistics_cn.nbs import index as nbs_index
from datafinder.engine.errors import SourceError


class FakeClient:
    """按分类树结构返回固定数据，并记录'网络请求'次数。"""

    def __init__(self, fail_on: str | None = None):
        self.calls = 0
        self.fail_on = fail_on
        self.tree_data = {
            ("6", ""): [{"id": "root", "name": "分省年度数据", "is_leaf": False, "root_id": "a" * 32}],
            ("6", "root"): [
                {"id": "cat1", "name": "工业", "is_leaf": False, "root_id": "a" * 32},
                {"id": "cat2", "name": "人口", "is_leaf": False, "root_id": "a" * 32},
            ],
            ("6", "cat1"): [
                {"id": "leaf1", "name": "规模以上工业企业主要指标", "is_leaf": True, "root_id": "a" * 32},
                {"id": "leaf2", "name": "工业产品产量", "is_leaf": True, "root_id": "a" * 32},
            ],
            ("6", "cat2"): [{"id": "leaf3", "name": "人口数", "is_leaf": True, "root_id": "a" * 32}],
            ("3", ""): [{"id": "r3", "name": "年度数据", "is_leaf": False, "root_id": "a" * 32}],
            ("3", "r3"): [{"id": "leaf4", "name": "国民经济核算", "is_leaf": True, "root_id": "a" * 32}],
        }
        self.inds = {
            "leaf1": [("i1", "规模以上工业企业单位数", "个"), ("i2", "规模以上工业企业利润总额", "亿元")],
            "leaf2": [("i3", "原煤产量", "万吨")],
            "leaf3": [("i4", "年末常住人口", "万人")],
            "leaf4": [("i5", "地区生产总值", "亿元"), ("i6", "第二产业增加值", "亿元")],
        }

    async def tree(self, code, pid=""):
        self.calls += 1
        return self.tree_data[(code, pid)]

    async def indicators(self, cid):
        self.calls += 1
        if cid == self.fail_on:
            raise SourceError("rate_limited", "出现访问防护验证页")
        return [{"id": i, "name": n, "unit": u, "mark": None} for i, n, u in self.inds[cid]]


@pytest.fixture
def conn(tmp_path):
    c = nbs_index.connect_index(tmp_path / "idx.db")
    yield c
    c.close()


async def test_build_walks_tree_and_stores_full_path(conn):
    res = await nbs_index.build(FakeClient(), conn, ["6", "3"])
    assert res.status == "done" and res.leaves_done == 4 and res.indicators_added == 6
    row = conn.execute("SELECT * FROM ind WHERE name='原煤产量'").fetchone()
    assert row["path"] == "分省年度数据 › 工业 › 工业产品产量"  # 库名不重复，数据集名在路径末尾
    assert row["root_id"] == "a" * 32
    assert nbs_index.stats(conn)["databases"]["6"]["indicators"] == 4


async def test_build_pauses_on_protection_page_and_resumes_without_redoing(conn):
    first = FakeClient(fail_on="leaf2")
    res = await nbs_index.build(first, conn, ["6"])
    assert res.status == "paused" and "稍后" in res.message
    done_before = conn.execute("SELECT COUNT(*) FROM done").fetchone()[0]
    assert done_before == 1  # leaf1 已保存
    second = FakeClient()
    res2 = await nbs_index.build(second, conn, ["6"])
    assert res2.status == "done" and res2.leaves_done == 2  # 只补 leaf2、leaf3
    assert conn.execute("SELECT COUNT(*) FROM ind").fetchone()[0] == 4


async def test_build_respects_request_budget(conn):
    c = FakeClient()
    res = await nbs_index.build(c, conn, ["6"], max_requests=3)
    assert res.status == "limited" and c.calls <= 4


async def test_build_rejects_unknown_database(conn):
    with pytest.raises(ValueError):
        await nbs_index.build(FakeClient(), conn, ["99"])


async def test_search_ranks_name_hits_and_ignores_region_words(conn):
    await nbs_index.build(FakeClient(), conn, ["6", "3"])
    hits = nbs_index.search(conn, "河北省工业企业利润")
    assert hits[0]["indicator"] == "规模以上工业企业利润总额"
    assert hits[0]["regional"] and hits[0]["db"] == "6" and hits[0]["cid"] == "leaf1"
    assert nbs_index.search(conn, "河北省工业企业利润", regional=False) == [] or all(
        not h["regional"] for h in nbs_index.search(conn, "河北省工业企业利润", regional=False)
    )
    assert nbs_index.search(conn, "完全不相关的词语") == []


async def test_search_regional_flag_and_db_filter(conn):
    await nbs_index.build(FakeClient(), conn, ["6", "3"])
    nat = nbs_index.search(conn, "生产总值", regional=False)
    assert nat and all(h["db"] == "3" for h in nat)
    assert nbs_index.search(conn, "生产总值", dbs=["6"]) == []
