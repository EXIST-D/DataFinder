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
from datafinder.domains.official_statistics_cn.yearbook.registry import Series, get_series
from datafinder.engine.http import Http

HTML = (Path(__file__).parents[3] / "fixtures" / "yearbook_toc.htm").read_text(encoding="utf-8")
BIG = HTML.replace("</ul></body>", "".join(f"<li><a href='h/{i}.jpg'>9-{i} 表{i}</a></li>" for i in range(30)) + "</ul></body>")


def series(**kw):
    base = dict(id="CN-SD", zh="山东统计年鉴", region="CN-SD", publisher="x", home="https://t.example/home",
                years=(2023, 2025), base="https://t.example/{year}/zk/", toc=("left.htm", "lefte.htm"))
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
    assert (distribution_for("https://x/a.jpg", "jpg").usability, distribution_for("https://x/a.jpg", "jpg").delivery) == ("image", "download")
    assert distribution_for("https://x/a.xls", "xls").usability == "tabular"
    assert distribution_for("https://x/a.HTM", "html").usability == "html_table"
    assert distribution_for("https://x/a.pdf", "pdf").usability == "document"
    odd = distribution_for("https://x/viewer?id=1", "other")
    assert odd.access_method == "page" and odd.delivery == "link"  # 认不出的格式不冒充文件


def test_table_entries_have_unique_ids_topics_regions_and_skip_notes():
    toc = parse_toc(HTML, "https://t.example/2025/zk/left.htm")
    toc.append(TocEntry("十四、工业", "14-1", "重复表号的另一张表", "https://t.example/x.jpg", "jpg", "table"))
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
    assert ok.delivery == "link" and ok.distributions[0].access_method == "page" and ok.access_status == "unknown"
    blocked = series_entry(series(), access_status="blocked", note="HTTP 403")
    assert blocked.distributions[0].access_method == "manual" and blocked.access_status == "blocked"
    assert edition_entry(series(), 2025, "u").landing_url == "https://t.example/2025/zk/indexch.htm"


# ───────────── 抓取 ─────────────


async def test_fetch_edition_falls_back_to_second_toc_name_and_rejects_non_toc_pages():
    def handler(req):
        return httpx.Response(404) if str(req.url).endswith("/left.htm") else httpx.Response(200, content=BIG.encode("utf-8"))

    status, url, toc, _ = await fetch_edition(http_for(handler), series(), 2025)
    assert status == "ok" and url.endswith("lefte.htm") and len(toc) > 30
    status, _, _, msg = await fetch_edition(http_for(lambda r: httpx.Response(200, text="<html>错误页</html>")), series(), 2025)
    assert status == "error" and "不像年鉴目录页" in msg


async def test_discover_records_editions_skips_missing_and_resumes(cat, patch_series):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(404) if "/2024/" in str(req.url) else httpx.Response(200, content=BIG.encode("gb18030"))

    patch_series(handler, [series()])
    res = await YearbookAdapter().discover(cat, DiscoverOptions())
    assert res.status == "done" and res.stopped == []
    assert cat.exists("yearbook.CN-SD:edition:2023") and cat.exists("yearbook.CN-SD:edition:2025")
    assert not cat.exists("yearbook.CN-SD:edition:2024")  # 404 的版次不收录
    assert cat.get("yearbook.CN-SD:series:CN-SD").title == "山东统计年鉴"
    first = len(seen)
    await YearbookAdapter().discover(cat, DiscoverOptions())  # 续跑：已收录版次不再请求（404 的版次会再探一次）
    assert len(seen) < first * 2 and sum("/2023/" in u for u in seen) == sum("/2023/" in u for u in seen[:first])


async def test_blocked_site_gives_up_that_series_only_and_marks_it(cat, patch_series):
    def handler(req):
        return httpx.Response(403) if "blocked.example" in str(req.url) else httpx.Response(200, content=BIG.encode("utf-8"))

    bad = series(id="CN-HE", zh="河北统计年鉴", region="CN-HE", base="https://blocked.example/{year}/zk/")
    good = series()
    patch_series(handler, [bad, good])
    res = await YearbookAdapter().discover(cat, DiscoverOptions())
    assert len(res.stopped) == 1 and "河北统计年鉴" in res.stopped[0]
    assert cat.get("yearbook.CN-SD:edition:2025") is not None  # 一个站点拒绝不影响其他系列
    he = cat.get("yearbook.CN-HE:series:CN-HE")
    assert he.access_status == "blocked" and he.delivery == "link" and he.distributions[0].access_method == "manual"
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
        if s.crawl:
            assert s.base and "{year}" in s.base and s.years and s.years[0] <= s.years[1]
            assert all(u.startswith("http") for u in s.toc_urls(s.years[1]))
    assert get_series("cn").edition_years(2023, 2030) == [2023, 2024, 2025]
    with pytest.raises(ValueError, match="未知年鉴系列"):
        get_series("nope")
