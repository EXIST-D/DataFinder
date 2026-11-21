import httpx
import pytest

from datafinder.catalog import Catalog
from datafinder.catalog.adapters import AdapterError, DiscoverOptions, FetchParams
from datafinder.domains.official_statistics_cn.hmt import tw
from datafinder.engine.http import Http

PAYLOAD = {
    "data": {
        "dataSets": [
            {
                "series": {
                    "0:0": {"observations": {"0": [10462359], "1": [11492384], "2": [0]}},
                    "1:0": {"observations": {"0": [102.74], "1": [104.55], "2": [0]}},
                }
            }
        ],
        "structure": {
            "name": "國內生產毛額及平減指數依支出分-年",
            "dimensions": {
                "series": [
                    {
                        "id": "fldid",
                        "values": [
                            {"id": "1", "name": "當期價格(新臺幣百萬元)"},
                            {"id": "3", "name": "平減指數(2021年=100)"},
                        ],
                    },
                    {"id": "code1", "values": [{"id": "1", "name": "1.民間消費"}]},
                ],
                "observation": [{"values": [{"id": "2022"}, {"id": "2023"}, {"id": "2024"}]}],
            },
        },
    }
}
# 注意：series 的键是各维度取值下标；这里 0:0 → 计价方式[0]、项目[0]；1:0 → 计价方式[1]、项目[0]


@pytest.fixture
def cat(tmp_path):
    c = Catalog.open(tmp_path / "c.db")
    yield c
    c.close()


async def test_discover_registers_verified_tables_without_network(cat):
    result = await tw.TwAdapter().discover(cat, DiscoverOptions())
    assert result.requests == 0 and result.added > 50
    e = next(cat.iter_all("tw.dgbas"), None)
    entries = [x for x in cat.iter_all("tw.dgbas") if x.kind == "dataset"]
    assert e and entries and all(x.delivery == "generate" for x in entries)
    gdp = next(x for x in entries if "国内生产毛额" in x.title)  # 繁体已转简体
    assert gdp.regions[0].region_id == "CN-TW" and "cnstat.macro" in {
        t.topic_id for t in gdp.topics
    }
    assert all("臺" not in x.title and "價" not in x.title for x in entries)


def test_tidy_table_has_per_series_units_and_drops_empty_periods():
    t = tw.to_table("A018102050", PAYLOAD, FetchParams(periods=8))
    assert {r[1] for r in t.rows} == {"2022", "2023"}  # 整期全是 0 的 2024 丢掉
    price = next(r for r in t.rows if "当期价格" in r[3] and r[1] == "2023")
    index = next(r for r in t.rows if "平减指数" in r[3] and r[1] == "2023")
    assert price[4] == 11492384.0 and price[5] == "新台币百万元" and price[2] == "台湾"
    assert index[4] == 104.55 and index[5] == "2021年=100"  # 单位跟着各自的序列走
    last = tw.to_table("A018102050", PAYLOAD, FetchParams(periods=1))
    assert {r[1] for r in last.rows} == {"2023"}


def test_missing_data_is_an_error():
    with pytest.raises(AdapterError, match="没有返回数据"):
        tw.to_table("x", {"data": {"dataSets": [], "structure": {}}}, FetchParams())


async def test_fetch_builds_the_url_and_rejects_html_error_pages(cat, monkeypatch):
    await tw.TwAdapter().discover(cat, DiscoverOptions())
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json=PAYLOAD)

    monkeypatch.setattr(
        tw, "Http", lambda **kw: Http(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    )
    entry = cat.get("tw.dgbas:dataset:A018102050")
    table = await tw.TwAdapter().fetch(cat, entry, FetchParams(start="2020", end="2023"))
    assert seen[0].endswith("sdmx/A018102050/&startTime=2020&endTime=2023") and len(table) > 0
    monkeypatch.setattr(
        tw,
        "Http",
        lambda **kw: Http(
            httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, text="功能代號不存在"))
            )
        ),
    )
    with pytest.raises(AdapterError, match="没有返回数据"):
        await tw.TwAdapter().fetch(cat, entry, FetchParams())
