"""官方网站栏目适配器（通用）：配置加载、首页导航发现、栏目逐页收录、续跑、失败标注。"""

import httpx
import pytest

from datafinder.catalog import Catalog
from datafinder.catalog.adapters import AdapterError, DiscoverOptions
from datafinder.catalog.sites import SiteAdapter, load_spec
from datafinder.engine.http import Http

SPEC = {
    "pages": 2,
    "nav_skip": "年鉴|新闻",
    "nav": [
        {"match": "统计公报|公报", "kind": "bulletin"},
        {"match": "数据发布|统计数据", "kind": "report"},
    ],
    "publishers": [
        {
            "id": "nbs",
            "name": "国家统计局",
            "region": "CN",
            "home": "https://s.example.gov.cn/",
            "columns": [
                {
                    "id": "release",
                    "title": "数据发布",
                    "url": "https://s.example.gov.cn/sj/zxfb/",
                    "kind": "report",
                    "pages": 3,
                },
                {
                    "id": "gb",
                    "title": "全国年度统计公报",
                    "url": "https://s.example.gov.cn/sj/gb/",
                    "kind": "bulletin",
                    "frequency": "annual",
                    "item_title": "统计公报 {text}",
                    "topics": ["cnstat.macro"],
                },
            ],
        },
        {
            "id": "CN-XX",
            "name": "某省统计局",
            "region": "CN-HE",
            "home": "https://t.example.gov.cn/",
            "discover_nav": True,
        },
    ],
}


def items(prefix, n, dated=True, start=0):
    return (
        "<ul>"
        + "".join(
            f'<li><a href="/{prefix}/2026{i:02d}/t2026{i:02d}01_{i}.html">2026年{i}月份国民经济运行情况{prefix}</a>'
            + (f"<span>2026-{i:02d}-01</span>" if dated else "")
            + "</li>"
            for i in range(start + 1, start + n + 1)
        )
        + "</ul>"
    )


def page(prefix, n, pages=1, **kw):
    return items(prefix, n, **kw) + f'<script>createPageHTML({pages}, 0, "index", "html");</script>'


HOME_T = """<a href="/tjsj/tjgb/">统计公报</a><a href="/tjsj/">统计数据</a>
<a href="/nj/">统计年鉴</a><a href="/xw/">新闻动态</a><a href="/art/2026/9/22/art_1_2.html">统计数据发布公告</a>
<a href="https://other.org/x/">统计数据平台</a>"""


@pytest.fixture
def cat(tmp_path):
    c = Catalog.open(tmp_path / "c.db")
    yield c
    c.close()


def adapter(handler, monkeypatch, spec=SPEC):
    from datafinder.catalog import sites

    a = SiteAdapter(load_spec(spec), "test_domain")
    monkeypatch.setattr(
        sites,
        "Http",
        lambda **kw: Http(httpx.AsyncClient(transport=httpx.MockTransport(handler))),
    )
    return a


def routes(table):
    seen = []

    def handler(req):
        key = str(req.url)
        seen.append(key)
        v = table.get(key)
        if v is None:
            return httpx.Response(404)
        if isinstance(v, int):
            return httpx.Response(v)
        return httpx.Response(200, content=v.encode())

    handler.seen = seen
    return handler


def test_load_spec_rejects_duplicate_publishers():
    bad = {"publishers": [{"id": "a", "name": "A", "region": "CN", "home": "https://a/"}] * 2}
    with pytest.raises(ValueError, match="重复"):
        load_spec(bad)


async def test_configured_columns_are_crawled_page_by_page(cat, monkeypatch):
    h = routes(
        {
            "https://s.example.gov.cn/": "<html></html>",
            "https://s.example.gov.cn/sj/zxfb/": page("zxfb", 3, pages=9),
            "https://s.example.gov.cn/sj/zxfb/index_1.html": page("zxfb", 3, start=3),
            "https://s.example.gov.cn/sj/zxfb/index_2.html": page("zxfb", 3, start=6),
            "https://s.example.gov.cn/sj/gb/": page("gb", 4),
        }
    )
    result = await adapter(h, monkeypatch).discover(cat, DiscoverOptions(params={"site": "nbs"}))
    assert result.status == "done" and result.added == 13
    # 数据发布栏目 pages=3：站点有 9 页，只翻 3 页
    assert not any("index_3" in u for u in h.seen)
    col = cat.get("site.nbs:portal:col/release")
    assert col.access_status == "supported" and col.verification_level == "observed"
    assert cat.get("site.nbs:portal:home").title == "国家统计局网站"
    assert cat.stats()["by_kind"]["report"] == 9
    bulletin = next(cat.iter_all("site.nbs"), None)
    gb = [e for e in cat.iter_all("site.nbs") if e.kind == "bulletin"]
    assert len(gb) == 4 and bulletin
    assert gb[0].title.startswith("统计公报 ") and gb[0].frequency == "annual"
    assert gb[0].edition_year == 2026 and "cnstat.macro" in {t.topic_id for t in gb[0].topics}
    assert gb[0].regions[0].region_id == "CN" and gb[0].delivery == "link"


async def test_nav_discovery_reads_home_and_skips_articles_files_and_other_sites(cat, monkeypatch):
    h = routes(
        {
            "https://t.example.gov.cn/": HOME_T,
            "https://t.example.gov.cn/tjsj/tjgb/": page("gb", 4),
            "https://t.example.gov.cn/tjsj/": "<html>栏目首页，没有列表</html>",
        }
    )
    a = adapter(h, monkeypatch)
    await a.discover(cat, DiscoverOptions(params={"site": "CN-XX"}))
    cols = [e for e in cat.iter_all("site.CN-XX") if e.kind == "portal" and e.source_key != "home"]
    assert sorted(e.title for e in cols) == ["某省统计局·统计公报", "某省统计局·统计数据"]
    gb = next(e for e in cols if "公报" in e.title)
    assert gb.access_status == "supported" and gb.extra["item_kind"] == "bulletin"
    plain = next(e for e in cols if "数据" in e.title)  # 没有列表：只作入口，不收条目
    assert plain.access_status == "supported" and plain.delivery == "link"
    assert cat.stats()["by_kind"]["bulletin"] == 4
    # 区域来自机构，而不是别处
    assert gb.regions[0].region_id == "CN-HE"


async def test_rerun_skips_finished_columns_and_refresh_drops_stale_nav(cat, monkeypatch):
    table = {
        "https://t.example.gov.cn/": HOME_T,
        "https://t.example.gov.cn/tjsj/tjgb/": page("gb", 4),
        "https://t.example.gov.cn/tjsj/": "<html></html>",
    }
    h = routes(table)
    a = adapter(h, monkeypatch)
    await a.discover(cat, DiscoverOptions(params={"site": "CN-XX"}))
    h.seen.clear()
    await a.discover(cat, DiscoverOptions(params={"site": "CN-XX"}))
    assert h.seen == ["https://t.example.gov.cn/"]  # 只重读首页导航，已完成的栏目不重抓
    # 首页改版：公报栏目消失 → 刷新后连同条目一起删除
    table["https://t.example.gov.cn/"] = '<a href="/tjsj/">统计数据</a>'
    await a.discover(cat, DiscoverOptions(params={"site": "CN-XX"}, refresh=True))
    assert cat.stats()["by_kind"].get("bulletin") is None
    assert [e.title for e in cat.iter_all("site.CN-XX") if e.source_key.startswith("col/")] == [
        "某省统计局·统计数据"
    ]


async def test_blocked_site_is_marked_manual_and_does_not_stop_others(cat, monkeypatch):
    h = routes(
        {
            "https://t.example.gov.cn/": 412,
            "https://s.example.gov.cn/": "<html></html>",
            "https://s.example.gov.cn/sj/zxfb/": page("zxfb", 3),
            "https://s.example.gov.cn/sj/gb/": 404,
        }
    )
    result = await adapter(h, monkeypatch).discover(cat, DiscoverOptions())
    assert any("某省统计局" in s and "412" in s for s in result.stopped)
    home = cat.get("site.CN-XX:portal:home")
    assert home.access_status == "blocked" and home.distributions[0].access_method == "manual"
    assert cat.stats()["by_kind"]["report"] == 3  # 另一个机构照常收录
    gb = cat.get("site.nbs:portal:col/gb")
    assert gb.access_status == "unavailable"  # 404 的栏目如实标注


async def test_unknown_site_and_request_budget(cat, monkeypatch):
    a = adapter(routes({}), monkeypatch)
    with pytest.raises(AdapterError, match="未知机构"):
        await a.discover(cat, DiscoverOptions(params={"site": "nope"}))
    h = routes(
        {
            "https://s.example.gov.cn/": "<html></html>",
            "https://s.example.gov.cn/sj/zxfb/": page("zxfb", 3),
        }
    )
    r = await adapter(h, monkeypatch).discover(
        cat, DiscoverOptions(params={"site": "nbs"}, max_requests=1)
    )
    assert r.status == "limited" and r.requests == 1


async def test_publisher_interval_is_passed_to_the_http_client(cat, monkeypatch):
    from datafinder.catalog import sites

    seen = {}

    def fake(**kw):
        seen.update(kw)
        return Http(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))))

    spec = {**SPEC, "publishers": [{**SPEC["publishers"][0], "interval": 2.5, "columns": []}]}
    monkeypatch.setattr(sites, "Http", fake)
    await SiteAdapter(load_spec(spec), "d").discover(cat, DiscoverOptions())
    assert seen["min_interval"] == {"s.example.gov.cn": 2.5} and seen["default_interval"] == 1.0


async def test_home_status_recovers_after_an_earlier_block(cat, monkeypatch):
    table = {"https://s.example.gov.cn/sj/zxfb/": 412}
    a = adapter(routes(table), monkeypatch)
    only_nbs = DiscoverOptions(params={"site": "nbs"})
    await a.discover(cat, only_nbs)
    assert cat.get("site.nbs:portal:home").access_status == "blocked"
    table["https://s.example.gov.cn/sj/zxfb/"] = page("zxfb", 3)
    await adapter(routes(table), monkeypatch).discover(cat, only_nbs)
    home = cat.get("site.nbs:portal:home")
    assert home.access_status == "supported" and home.distributions[0].access_method == "page"


async def test_title_filtered_columns_keep_paging_past_pages_without_matches(cat, monkeypatch):
    def pg(prefix, titles, start):
        return "".join(
            f'<li><a href="/{prefix}/202601/t2026010{start + i}_{start + i}.html">{t}</a><span>2026-01-0{i + 1}</span></li>'
            for i, t in enumerate(titles)
        )

    plain = ["普通图书甲号", "普通图书乙号", "普通图书丙号"]
    yb = ["中国统计年鉴-2021", "中国农村统计年鉴-2021", "中国能源统计年鉴2018"]
    h = routes(
        {
            "https://s.example.gov.cn/sj/zxfb/": pg("b", yb[:1] + plain[:2], 1)
            + '<script>createPageHTML(4, 0, "index", "html");</script>',
            "https://s.example.gov.cn/sj/zxfb/index_1.html": pg("b", plain, 4),  # 整页都不匹配
            "https://s.example.gov.cn/sj/zxfb/index_2.html": pg("b", yb[1:] + plain[:1], 7),
            "https://s.example.gov.cn/sj/zxfb/index_3.html": 404,
        }
    )
    spec = {
        **SPEC,
        "publishers": [
            {
                **SPEC["publishers"][0],
                "columns": [
                    {
                        "id": "books",
                        "title": "书目",
                        "url": "https://s.example.gov.cn/sj/zxfb/",
                        "kind": "reference",
                        "pages": 4,
                        "text": "年鉴",
                    }
                ],
            }
        ],
    }
    await adapter(h, monkeypatch, spec).discover(cat, DiscoverOptions(params={"site": "nbs"}))
    titles = sorted(e.title for e in cat.iter_all("site.nbs") if e.kind == "reference")
    assert titles == ["中国农村统计年鉴-2021", "中国统计年鉴-2021", "中国能源统计年鉴2018"]


async def test_home_entry_follows_config_scope_but_keeps_crawled_status(cat, monkeypatch):
    table = {"https://s.example.gov.cn/sj/zxfb/": page("zxfb", 3)}
    await adapter(routes(table), monkeypatch).discover(cat, DiscoverOptions(params={"site": "nbs"}))
    assert cat.get("site.nbs:portal:home").access_status == "supported"
    spec = {
        **SPEC,
        "publishers": [{**SPEC["publishers"][0], "scope": "货币政策 社会融资规模", "columns": []}],
    }
    await adapter(routes({}), monkeypatch, spec).discover(
        cat, DiscoverOptions(params={"site": "nbs"})
    )
    home = cat.get("site.nbs:portal:home")
    assert (
        home.path == "货币政策 社会融资规模" and home.access_status == "supported"
    )  # 职责词刷新，状态不丢
