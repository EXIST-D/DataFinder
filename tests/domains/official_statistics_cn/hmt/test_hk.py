import json

import httpx
import pytest

from datafinder.catalog import Catalog
from datafinder.catalog.adapters import AdapterError, DiscoverOptions, FetchParams
from datafinder.domains.official_statistics_cn.hmt import hk
from datafinder.engine.http import Http


def catalog_page(datasets):
    ld = {"@graph": [{"@type": "WebPage"}, {"@type": "DataCatalog", "dataset": datasets}]}
    return f'<html><script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script></html>'


DATASETS = [
    {
        "identifier": "110-01001",
        "name": "按性别及年龄组别划分的人口",
        "keywords": ["人口与住户", "人口估计", "110-01001"],
    },
    {
        "identifier": "210-06001",
        "name": "零售业销货额",
        "keywords": ["行业", "零售业", "210-06001"],
    },
]

PAYLOAD = {
    "header": {
        "status": {"code": 0, "name": "Success"},
        "title": "按性别及年龄组别划分的人口",
        "tablenote": ["数字为年中数"],
    },
    "dataSet": [
        {
            "SEX": "",
            "SEXDesc": "Total",
            "freq": "H",
            "period": "202506",
            "sv": "POP",
            "svDesc": "数目 ('000)",
            "figure": 7498.9,
        },
        {
            "SEX": "",
            "SEXDesc": "Total",
            "freq": "H",
            "period": "202512",
            "sv": "POP",
            "svDesc": "数目 ('000)",
            "figure": 7508.7,
        },
        {
            "SEX": "M",
            "SEXDesc": "男",
            "freq": "H",
            "period": "202512",
            "sv": "POP",
            "svDesc": "数目 ('000)",
            "figure": 3500.1,
        },
        {
            "SEX": "M",
            "SEXDesc": "男",
            "freq": "H",
            "period": "202512",
            "sv": "PCT",
            "svDesc": "占总人口的百分比(%)",
            "figure": 46.6,
        },
        {
            "SEX": "F",
            "SEXDesc": "女",
            "freq": "H",
            "period": "202512",
            "sv": "POP",
            "svDesc": "数目 ('000)",
            "figure": None,
        },
        {
            "SEX": "",
            "SEXDesc": "Total",
            "freq": "Y",
            "period": "2024",
            "sv": "POP",
            "svDesc": "数目 ('000)",
            "figure": 7400,
        },
    ],
}


@pytest.fixture
def cat(tmp_path):
    c = Catalog.open(tmp_path / "c.db")
    yield c
    c.close()


def patch_http(monkeypatch, handler):
    monkeypatch.setattr(
        hk, "Http", lambda **kw: Http(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    )


async def test_discover_reads_the_json_ld_catalog_and_classifies(cat, monkeypatch):
    patch_http(monkeypatch, lambda r: httpx.Response(200, text=catalog_page(DATASETS)))
    result = await hk.HkAdapter().discover(cat, DiscoverOptions())
    assert result.added == 2
    e = cat.get("hk.csd:dataset:110-01001")
    assert e.delivery == "generate" and e.distributions[0].access_method == "api"
    assert e.regions[0].region_id == "CN-HK" and e.path == "人口与住户 › 人口估计"
    assert "cnstat.population" in {t.topic_id for t in e.topics}
    assert "cnstat.commerce" in {
        t.topic_id for t in cat.get("hk.csd:dataset:210-06001").topics
    }  # 栏目名补主题
    assert "使用条款" in e.license and cat.get("hk.csd:portal:home")


async def test_discover_fails_loudly_when_the_page_changes(cat, monkeypatch):
    patch_http(monkeypatch, lambda r: httpx.Response(200, text="<html>改版了</html>"))
    with pytest.raises(AdapterError, match="没有解析出统计表"):
        await hk.HkAdapter().discover(cat, DiscoverOptions())


def test_tidy_table_keeps_last_periods_per_frequency_and_drops_empty_figures():
    t = hk.to_table("110-01001", PAYLOAD, FetchParams(periods=1))
    keys = {(r[1], r[3]) for r in t.rows}
    # 半年度只留最近一期、年度也留最近一期；空值丢掉
    assert {r[1] for r in t.rows} == {"202512", "2024"}
    assert all(r[4] != "" for r in t.rows) and len(t.rows) == 4
    male_pct = next(r for r in t.rows if "男" in r[3] and r[5].startswith("占总人口"))
    assert (
        male_pct[3] == "按性别及年龄组别划分的人口 · 男 · 占总人口的百分比(%)"
    )  # 多个度量时指标里带度量名
    total = next(
        r for r in t.rows if r[1] == "202512" and "男" not in r[3] and r[5].startswith("数目")
    )
    assert total[2] == "香港" and total[4] == 7508.7 and keys


def test_explicit_range_filters_by_period_prefix():
    t = hk.to_table("110-01001", PAYLOAD, FetchParams(start="2025", end="2025"))
    assert {r[1] for r in t.rows} == {"202506", "202512"}


def test_failed_status_is_an_error():
    bad = {"header": {"status": {"code": 3, "description": "表号不存在"}}, "dataSet": []}
    with pytest.raises(AdapterError, match="表号不存在"):
        hk.to_table("x", bad, FetchParams())


async def test_fetch_calls_the_api_once_and_rejects_non_tables(cat, monkeypatch):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json=PAYLOAD)

    patch_http(monkeypatch, handler)
    entry = hk.table_entry(DATASETS[0])
    table = await hk.HkAdapter().fetch(cat, entry, FetchParams(periods=2))
    assert (
        len(seen) == 1
        and "id=110-01001" in seen[0]
        and "full_series=1" in seen[0]
        and len(table) > 0
    )
    with pytest.raises(AdapterError, match="不能取数"):
        await hk.HkAdapter().fetch(cat, hk.home_entry(), FetchParams())
