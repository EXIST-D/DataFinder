
import pytest

from datafinder import service
from datafinder.service import ServiceError
from datafinder.tables import Table


def test_plan_sources_hebei_industry_picks_region_and_domain_portals(portals):
    plan = service.plan_sources("河北省工业质量体系评价")
    u = plan["understanding"]
    assert u["region_codes"] == ["CN-HE"] and "industry" in u["domains"] and u["country"] == "CN"
    regional = [p["name"] for p in plan["sources"]["regional_portals"]["items"]]
    assert "河北省统计局" in regional
    official = [p["name"] for p in plan["sources"]["official_portals"]["items"]]
    assert "工业和信息化部" in official and "US Industry" not in official  # 中文主题默认只看中国
    assert plan["sources"]["direct"]["hint"]  # 没建索引：如实说明，不假装有指标
    assert any("评价" in h or "指标体系" in h for h in plan["analysis_hints"])  # 评价类题目有专门建议
    assert plan["caveats"]


def test_plan_sources_with_index_gives_fetch_args(portals):
    from datafinder.nbs import index as nbs_index

    conn = nbs_index.connect_index()
    conn.execute("INSERT INTO ind VALUES ('6', ?, ?, '分省年度数据 › 工业', ?, '规模以上工业企业利润总额', '亿元', NULL)",
                 ("c" * 32, "r" * 32, "i" * 32))
    conn.commit()
    conn.close()
    plan = service.plan_sources("河北省工业企业利润")
    (item,) = [i for i in plan["sources"]["direct"]["items"] if i["db"] == "6"]
    assert item["fetch"] == {"tool": "nbs_fetch", "args": {"db": "6", "cid": "c" * 32, "indicators": ["i" * 32], "regions": ["河北"]}}


def test_plan_sources_errors_are_actionable():
    with pytest.raises(ServiceError, match="datafinder sync"):
        service.plan_sources("工业")
    with pytest.raises(ServiceError, match="不能为空"):
        service.plan_sources("  ")


def test_portals_list_filters(portals):
    r = service.portals_list(label="ministry")
    assert r["total"] == 1 and r["portals"][0]["name"] == "工业和信息化部"
    assert service.portals_list(region="CN-HE")["portals"][0]["verify"] == "unverified"


async def test_nbs_fetch_end_to_end_with_stub_client(monkeypatch):
    from contextlib import asynccontextmanager

    class Stub:
        async def indicators(self, cid):
            return [{"id": "a" * 32, "name": "工业增加值", "unit": "亿元", "mark": None}]

        async def regions(self, cid):
            return {"catalogs": [{"id": "x", "name": "全部地区"}],
                    "regions": [{"text": "河北省", "value": "130000000000"}, {"text": "北京市", "value": "110000000000"}]}

        async def tree(self, db, pid=""):
            return [{"id": "dbnode", "name": "分省年度数据", "is_leaf": False, "root_id": "a" * 32}]

        async def table(self, **kw):
            assert kw["root_id"] == "a" * 32 and [a["text"] for a in kw["areas"]] == ["河北省"]
            return [{"期别": "2023年", "期别代码": "2023YY", "地区": "河北省", "指标": "工业增加值", "数值": "100", "单位": "亿元"},
                    {"期别": "2024年", "期别代码": "2024YY", "地区": "河北省", "指标": "工业增加值", "数值": "110", "单位": "亿元"}]

    @asynccontextmanager
    async def fake():
        yield Stub()

    monkeypatch.setattr("datafinder.service.nbs._nbs", fake)
    t = await service.nbs_fetch("6", "c" * 32, ["工业增加值"], ["河北"])
    assert isinstance(t, Table) and t.rows[0][4] == 100.0 and "国家统计局" in t.meta["来源"]
    out = service.table_result(t, fmt="csv")
    assert out["saved_to"].endswith(".csv") and out["describe"]["series"][0]["cagr"] == pytest.approx(0.1)
    with pytest.raises(ServiceError, match="需要指定 regions"):
        await service.nbs_fetch("6", "c" * 32, ["工业增加值"], None)


async def test_nbs_fetch_validates_before_any_network(monkeypatch):
    def boom():
        raise AssertionError("不该联网")

    monkeypatch.setattr("datafinder.service.nbs._nbs", boom)
    with pytest.raises(ServiceError, match="未知数据库"):
        await service.nbs_fetch("99", "c" * 32, ["x"], ["北京"])
    with pytest.raises(ServiceError, match="cid 格式"):
        await service.nbs_fetch("6", "bad", ["x"], ["北京"])
    with pytest.raises(ServiceError, match="不能为空"):
        await service.nbs_fetch("6", "c" * 32, [], ["北京"])
