from pathlib import Path

import httpx
import pytest

from datafinder.catalog import Catalog
from datafinder.catalog.adapters import AdapterError, DiscoverOptions
from datafinder.domains.official_statistics_cn.yearbook import adapter as yb_adapter
from datafinder.domains.official_statistics_cn.yearbook.adapter import (
    YearbookAdapter,
    fetch_edition,
)
from datafinder.domains.official_statistics_cn.yearbook.entries import (
    distribution_for,
    edition_entry,
    series_entry,
    table_entries,
)
from datafinder.domains.official_statistics_cn.yearbook.parse import TocEntry, parse_toc
from datafinder.domains.official_statistics_cn.yearbook.registry import Edition, Series, get_series
from datafinder.engine.http import Http

HTML = (Path(__file__).parents[3] / "fixtures" / "yearbook_toc.htm").read_text(encoding="utf-8")
BIG = HTML.replace(
    "</ul></body>",
    "".join(f"<li><a href='h/{i}.jpg'>9-{i} 表{i}</a></li>" for i in range(30)) + "</ul></body>",
)


def series(**kw):
    base = dict(
        id="CN-SD",
        zh="山东统计年鉴",
        region="CN-SD",
        publisher="x",
        home="https://t.example/home",
        years=(2023, 2025),
        base="https://t.example/{year}/zk/",
        toc=("left.htm", "lefte.htm"),
    )
    return Series(**{**base, **kw})


def http_for(handler):
    return Http(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.fixture
def cat(tmp_path):
    c = Catalog.open(tmp_path / "c.db")
    yield c
    c.close()


@pytest.fixture
def patch_series(monkeypatch):
    def install(handler, series_list):
        monkeypatch.setattr(yb_adapter, "Http", lambda **kw: http_for(handler))
        monkeypatch.setattr(yb_adapter, "load_series", lambda: series_list)

    return install


# ───────────── 条目构造 ─────────────


def test_distribution_formats_and_usability():
    assert (
        distribution_for("https://x/a.jpg", "jpg").usability,
        distribution_for("https://x/a.jpg", "jpg").delivery,
    ) == ("image", "download")
    assert distribution_for("https://x/a.xls", "xls").usability == "tabular"
    assert distribution_for("https://x/a.HTM", "html").usability == "html_table"
    assert distribution_for("https://x/a.pdf", "pdf").usability == "document"
    odd = distribution_for("https://x/viewer?id=1", "other")
    assert odd.access_method == "page" and odd.delivery == "link"  # 认不出的格式不冒充文件


def test_table_entries_have_unique_ids_topics_regions_and_skip_notes():
    toc = parse_toc(HTML, "https://t.example/2025/zk/left.htm")
    toc.append(
        TocEntry(
            "十四、工业", "14-1", "重复表号的另一张表", "https://t.example/x.jpg", "jpg", "table"
        )
    )
    entries = table_entries(series(), 2025, toc)
    ids = [e.resource_id for e in entries]
    assert len(ids) == len(set(ids)) and all(e.kind == "table" for e in entries)
    assert any(i.endswith("14-1#2") for i in ids)  # 同一版次里表号重复：加后缀保证唯一
    ind = next(e for e in entries if e.title.startswith("2015-2024年规模以上工业增加值"))
    assert {"cnstat.industry", "cnstat.industry.value_added"} <= {t.topic_id for t in ind.topics}
    assert ind.regions[0].region_id == "CN-SD" and ind.regions[0].evidence_status == "structure"
    assert ind.edition_year == 2025 and ind.period_start is None  # 出版年≠观测期：观测期不填
    assert ind.delivery == "download" and ind.distributions[0].usability == "image"


def test_series_entry_is_navigation_only_when_blocked():
    ok = series_entry(series())
    assert (
        ok.delivery == "link"
        and ok.distributions[0].access_method == "page"
        and ok.access_status == "unknown"
    )
    blocked = series_entry(series(), access_status="blocked", note="HTTP 403")
    assert blocked.distributions[0].access_method == "manual" and blocked.access_status == "blocked"
    assert edition_entry(series(), 2025, "u").landing_url == "https://t.example/2025/zk/indexch.htm"


# ───────────── 抓取 ─────────────


async def test_fetch_edition_falls_back_to_second_toc_name_and_rejects_non_toc_pages():
    def handler(req):
        return (
            httpx.Response(404)
            if str(req.url).endswith("/left.htm")
            else httpx.Response(200, content=BIG.encode("utf-8"))
        )

    status, url, toc, _ = await fetch_edition(http_for(handler), series(), 2025)
    assert status == "ok" and url.endswith("lefte.htm") and len(toc) > 30
    status, _, _, msg = await fetch_edition(
        http_for(lambda r: httpx.Response(200, text="<html>错误页</html>")), series(), 2025
    )
    assert status == "error" and "不像年鉴目录页" in msg


async def test_discover_records_editions_skips_missing_and_resumes(cat, patch_series):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return (
            httpx.Response(404)
            if "/2024/" in str(req.url)
            else httpx.Response(200, content=BIG.encode("gb18030"))
        )

    patch_series(handler, [series()])
    res = await YearbookAdapter().discover(cat, DiscoverOptions())
    assert res.status == "done" and res.stopped == []
    assert cat.exists("yearbook.CN-SD:edition:2023") and cat.exists("yearbook.CN-SD:edition:2025")
    assert not cat.exists("yearbook.CN-SD:edition:2024")  # 404 的版次不收录
    assert cat.get("yearbook.CN-SD:series:CN-SD").title == "山东统计年鉴"
    first = len(seen)
    await YearbookAdapter().discover(
        cat, DiscoverOptions()
    )  # 续跑：已收录版次不再请求（404 的版次会再探一次）
    assert len(seen) < first * 2 and sum("/2023/" in u for u in seen) == sum(
        "/2023/" in u for u in seen[:first]
    )


async def test_blocked_site_gives_up_that_series_only_and_marks_it(cat, patch_series):
    def handler(req):
        return (
            httpx.Response(403)
            if "blocked.example" in str(req.url)
            else httpx.Response(200, content=BIG.encode("utf-8"))
        )

    bad = series(
        id="CN-HE", zh="河北统计年鉴", region="CN-HE", base="https://blocked.example/{year}/zk/"
    )
    good = series()
    patch_series(handler, [bad, good])
    res = await YearbookAdapter().discover(cat, DiscoverOptions())
    assert len(res.stopped) == 1 and "河北统计年鉴" in res.stopped[0]
    assert cat.get("yearbook.CN-SD:edition:2025") is not None  # 一个站点拒绝不影响其他系列
    he = cat.get("yearbook.CN-HE:series:CN-HE")
    assert (
        he.access_status == "blocked"
        and he.delivery == "link"
        and he.distributions[0].access_method == "manual"
    )
    assert not cat.exists("yearbook.CN-HE:edition:2025")


async def test_navigation_only_series_are_registered_without_network(cat, patch_series):
    patch_series(lambda r: pytest.fail("不该联网"), [series(crawl=False, base=None, years=None)])
    res = await YearbookAdapter().discover(cat, DiscoverOptions())
    assert res.status == "done" and res.added == 0
    assert cat.get("yearbook.CN-SD:series:CN-SD").delivery == "link"


async def test_unknown_series_is_a_clear_error(cat):
    with pytest.raises(AdapterError, match="未知年鉴系列"):
        await YearbookAdapter().discover(cat, DiscoverOptions(params={"series": "NOPE"}))


def test_registry_is_consistent():
    from datafinder.domains.official_statistics_cn.yearbook.registry import load_series

    all_series = load_series()
    ids = [s.id for s in all_series]
    assert len(ids) == len(set(ids)) and "cn" in ids
    for s in all_series:
        assert s.home.startswith("http")
        if s.crawl and s.editions:  # 普查资料：逐版列出
            assert s.type == "census" and all(e.base.startswith("http") for e in s.editions)
            assert all(u.startswith("http") for u in s.toc_urls(s.edition_years()[0]))
        elif s.crawl and s.format == "list":
            assert s.list_urls and all(u.startswith("http") for u in s.list_urls)
        elif s.crawl:
            assert s.base and s.years and s.years[0] <= s.years[1]
            assert s.format in ("zk", "tree")
            assert s.format == "tree" or "{year}" in s.base
            assert all(u.startswith("http") for u in s.toc_urls(s.years[1]))
    assert get_series("cn").edition_years(2023, 2030) == [2023, 2024, 2025]
    with pytest.raises(ValueError, match="未知年鉴系列"):
        get_series("nope")


async def test_alternate_tocs_are_merged_so_excel_comes_first():
    xls = BIG.replace(".jpg", ".xls")

    def handler(req):
        return httpx.Response(
            200, content=(xls if str(req.url).endswith("lefte.htm") else BIG).encode()
        )

    status, _, toc, _ = await fetch_edition(http_for(handler), series(), 2025)
    assert status == "ok"
    first = next(t for t in toc if t.kind == "table" and t.no == "1-2")
    assert dict(first.others)["https://t.example/2025/zk/html/C01-02.xls"] == "xls"
    entry = next(e for e in table_entries(series(), 2025, toc) if e.extra["no"] == "1-2")
    assert [d.file_format for d in entry.distributions] == ["xls", "jpg"]  # 能直接分析的在前
    assert entry.distributions[0].usability == "tabular"


async def test_mismatched_alternate_toc_is_not_merged():
    other = (
        "<ul><li id='foldheader'>一、x</li>"
        + "".join(f"<li><a href='o/{i}.xls'>7-{i} 别的表{i}</a></li>" for i in range(30))
        + "</ul>"
    )

    def handler(req):
        return httpx.Response(
            200, content=(other if str(req.url).endswith("lefte.htm") else BIG).encode()
        )

    _, _, toc, _ = await fetch_edition(http_for(handler), series(), 2025)
    assert all(not t.others for t in toc)  # 对不上号就不张冠李戴


def test_census_series_use_their_own_source_prefix_kind_and_topics():
    from datafinder.domains.official_statistics_cn.yearbook.entries import source_id

    census = Series(
        id="pop",
        zh="全国人口普查资料",
        region="CN",
        publisher="x",
        home="https://t.example/",
        type="census",
        topics=("cnstat.population",),
        editions=(
            Edition(
                2020,
                "https://t.example/7rp/zk/",
                "第七次（2020年）",
                "https://t.example/7rp/indexch.htm",
            ),
        ),
    )
    assert source_id(census) == "census.pop" and census.edition_years() == [2020]
    ed = edition_entry(census, 2020, "u")
    assert ed.title == "全国人口普查资料 第七次（2020年）" and ed.landing_url.endswith(
        "7rp/indexch.htm"
    )
    toc = parse_toc(HTML, "https://t.example/7rp/zk/left.htm")
    tables = table_entries(census, 2020, toc)
    assert {e.kind for e in tables} == {"census_product"}
    assert all("cnstat.population" in {t.topic_id for t in e.topics} for e in tables)


# ───────────────────────── 非 zk 模板：树形目录（上海）与版次列表（广东、湖北）─────────────────────────

TREE = """<ul>
<li><div class="treeview-folder"> 第一篇——综合</div><ul>
 <li><div class="treeview-file"><a href="nj25.htm?d1=2025tjnj/zbhtml/C0001.htm">简要说明</a></div></li>
 <li><div class="treeview-file"><a href="nj25.htm?d1=2025tjnj/C0101.htm">表1.1 行政区划(2024)</a></div></li>
 <li><div class="treeview-folder">指标解释</div><ul><li><a href="nj25.htm?d1=2025tjnj/zbhtml/C0107.htm">指 数</a></li></ul></li>
</ul></li>
<li><div class="treeview-folder"> 第二篇——人口、就业与工资</div><ul>
 <li><div class="treeview-file"><a href="nj25.htm?d1=2025tjnj/C0201.htm">表2.1 户数、人口、人口密度（1978～2024）</a></div></li>
 <li><div class="treeview-file"><a href="nj25.htm?d1=2025tjnj/C0202.htm">2.2各区常住人口（2024）</a></div></li>
 <li><div class="treeview-file"><a href="nj25.htm?d1=2025tjnj/zbhtml/C0301.htm">图1 上海市生产总值</a></div></li>
</ul></li></ul>"""


def test_tree_toc_maps_viewer_links_to_real_files_and_keeps_chapters():
    from datafinder.domains.official_statistics_cn.yearbook.parse import parse_tree_toc

    toc = parse_tree_toc(TREE, "https://t.example/tjnj/tjnj2025.htm")
    tables = [t for t in toc if t.kind == "table"]
    assert [t.no for t in tables] == ["1.1", "2.1", "2.2"]
    assert tables[0].url == "https://t.example/tjnj/2025tjnj/C0101.htm" and tables[0].fmt == "html"
    assert tables[0].chapter == "第一篇——综合" and tables[2].chapter == "第二篇——人口、就业与工资"
    notes = [t.title for t in toc if t.kind == "note"]
    assert "简要说明" in notes and "图1 上海市生产总值" in notes  # 图与说明不当作数据表


async def test_tree_series_is_fetched_with_year_in_toc_name(cat, patch_series):
    urls = []
    big = TREE.replace(
        "</ul></li></ul>",
        "".join(
            f"<li><a href='nj25.htm?d1=2025tjnj/C09{i:02d}.htm'>表9.{i} 表{i}</a></li>"
            for i in range(30)
        )
        + "</ul></li></ul>",
    )

    def handler(req):
        urls.append(str(req.url))
        return httpx.Response(200, content=big.encode())

    sh = series(
        id="CN-SH",
        format="tree",
        years=(2025, 2025),
        base="https://t.example/tjnj/",
        toc=("tjnj{year}.htm",),
        landing="https://t.example/tjnj/tjnj{year}.htm",
    )
    patch_series(handler, [sh])
    await YearbookAdapter().discover(cat, DiscoverOptions())
    assert urls == ["https://t.example/tjnj/tjnj2025.htm"]
    ed = cat.get("yearbook.CN-SH:edition:2025")
    assert ed and ed.landing_url == "https://t.example/tjnj/tjnj2025.htm"
    assert cat.stats()["by_kind"]["table"] == 33


LIST_PAGE = """<a href="/gdtjnj/content/post_2.html">广东统计年鉴2025年</a>
<a href="/gdtjnj/content/post_1.html">广东统计年鉴2024年</a>
<a href="/gdtjnj/">广东统计年鉴</a><a href="/x/y.html">年度报告2025</a>"""
EDITION_PAGE = """<a href="http://t.example/online/2025/">在线浏览</a>
<a href="/attachment/0/630/2.zip">2025广东统计年鉴光盘内容.zip</a>"""


async def test_list_series_registers_editions_with_attachments_and_online_links(cat, patch_series):
    def handler(req):
        path = req.url.path
        if path == "/gdtjnj/":
            return httpx.Response(200, content=LIST_PAGE.encode())
        if path.endswith("post_2.html"):
            return httpx.Response(200, content=EDITION_PAGE.encode())
        return httpx.Response(200, text="<html>无附件</html>")

    gd = series(
        id="CN-GD",
        format="list",
        follow=True,
        years=None,
        base=None,
        list_urls=("https://t.example/gdtjnj/",),
    )
    patch_series(handler, [gd])
    result = await YearbookAdapter().discover(cat, DiscoverOptions())
    assert result.added == 2
    e25 = cat.get("yearbook.CN-GD:edition:2025")
    assert [(d.access_method, d.file_format) for d in e25.distributions][:2] == [
        ("direct_file", "zip"),
        ("page", None),
    ]
    assert (
        e25.delivery == "download" and e25.distributions[0].usability is None
    )  # 压缩包不冒充可直接分析
    e24 = cat.get("yearbook.CN-GD:edition:2024")
    assert e24.delivery == "link"  # 没找到附件：只给版次页
    # 只收有年份的"统计年鉴"链接；再次运行不重复请求
    assert cat.stats()["by_kind"]["edition"] == 2


async def test_list_series_whose_link_is_the_file_itself(cat, patch_series):
    page = '<a href="/q/2025.zip">2025年统计年鉴</a>'
    hb = series(
        id="CN-HB", format="list", years=None, base=None, list_urls=("https://t.example/q/",)
    )
    patch_series(lambda req: httpx.Response(200, content=page.encode()), [hb])
    await YearbookAdapter().discover(cat, DiscoverOptions())
    e = cat.get("yearbook.CN-HB:edition:2025")
    assert [d.access_method for d in e.distributions] == [
        "direct_file"
    ]  # 不重复一个指向同一文件的页面入口
