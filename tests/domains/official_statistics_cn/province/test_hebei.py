"""河北指标查询适配器：只登记元数据（指标名称/口径/单位/入口），不存数值。"""

from __future__ import annotations

import pytest

from datafinder.catalog import Catalog
from datafinder.catalog.adapters import DiscoverOptions
from datafinder.domains.official_statistics_cn.province import hebei

TREE = [
    {
        "NAME": "工业",
        "children": [
            {
                "NAME": "工业生产",
                "children": [
                    {"NAME": "工业总产值", "ID": "x1", "DW_TEXT": "万元", "ZBPD_TEXT": "季度"},
                    {"NAME": "工业企业单位数", "ID": "x2", "DW_TEXT": "个", "ZBPD_TEXT": "月度"},
                ],
            }
        ],
    },
    {
        "NAME": "综合",
        "children": [{"NAME": "地区生产总值", "ID": "x3", "DW_TEXT": "亿元", "ZBPD_TEXT": "年度"}],
    },
]


@pytest.fixture
def cat(tmp_path):
    c = Catalog.open(tmp_path / "c.db")
    yield c
    c.close()


class _FakeHttp:
    payload: dict = {"success": True, "data": TREE}
    calls = 0

    def __init__(self, **_kw):
        pass

    async def post_json(self, url, **_kw):
        type(self).calls += 1
        return type(self).payload

    async def aclose(self):
        return None


@pytest.fixture
def fake_http(monkeypatch):
    _FakeHttp.payload = {"success": True, "data": TREE}
    _FakeHttp.calls = 0
    monkeypatch.setattr(hebei, "Http", _FakeHttp)
    return _FakeHttp


async def test_discover_registers_platforms_and_indicator_metadata(cat, fake_http):
    res = await hebei.HebeiQueryAdapter().discover(cat, DiscoverOptions())

    assert res.requests == 1 and fake_http.calls == 1
    db = cat.get("province.hebei:database:indicators")
    assert db is not None and db.title == "河北省指标查询"

    ind = cat.get("province.hebei:indicator:x1")
    assert ind is not None
    assert (ind.title, ind.unit, ind.frequency) == ("工业总产值", "万元", "quarterly")
    assert ind.path == "工业 / 工业生产"  # 中间节点只用来拼路径
    assert ind.parent_id == db.resource_id
    assert ind.topics and ind.regions[0].region_id == "CN-HE"
    # 只给入口，不冒充可下载
    assert [d.access_method for d in ind.distributions] == ["page"]
    assert ind.delivery == "link"
    assert ind.extra["code"] == "x1" and ind.extra["platform"] == "dsfa"


async def test_annual_indicators_map_to_annual_and_panels_cover_each_year(cat, fake_http):
    await hebei.HebeiQueryAdapter().discover(cat, DiscoverOptions())
    assert cat.get("province.hebei:indicator:x3").frequency == "annual"

    panels = cat.get("province.hebei:database:panels")
    years = hebei.PANEL_YEARS
    assert len(panels.distributions) == years[1] - years[0] + 1
    assert all(
        str(y) in panels.distributions[i].url
        for i, y in enumerate(range(years[1], years[0] - 1, -1))
    )


async def test_platform_error_still_registers_the_two_entrances(cat, fake_http):
    fake_http.payload = {"success": False, "message": "服务器异常", "state": 20001}
    res = await hebei.HebeiQueryAdapter().discover(cat, DiscoverOptions())

    assert res.stopped and "服务器异常" in res.stopped[0]
    assert cat.get("province.hebei:database:indicators") is not None
    assert cat.get("province.hebei:database:panels") is not None
    assert cat.get("province.hebei:indicator:x1") is None  # 拿不到树就不臆造指标
