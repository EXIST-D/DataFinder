from pathlib import Path

import httpx
import pytest

from datafinder.domains.official_statistics_cn.yearbook import crawl
from datafinder.domains.official_statistics_cn.yearbook import index as yb
from datafinder.domains.official_statistics_cn.yearbook.registry import (
    Series,
    get_series,
    load_series,
)
from datafinder.engine.http import Http

HTML = (Path(__file__).parents[3] / "fixtures" / "yearbook_toc.htm").read_text(encoding="utf-8")
BIG = HTML.replace("</ul></body>", "".join(f"<li><a href='h/{i}.jpg'>9-{i} 表{i}</a></li>" for i in range(30)) + "</ul></body>")


def series(**kw):
    base = dict(id="t", zh="测试年鉴", region="CN-XX", publisher="x", home="https://t.example", years=(2023, 2025),
                base="https://t.example/{year}/zk/", toc=("left.htm", "lefte.htm"))
    return Series(**{**base, **kw})


def http_for(handler):
    return Http(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_build_records_ok_missing_and_resumes_without_refetching():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if "/2024/" in str(req.url):
            return httpx.Response(404)
        return httpx.Response(200, content=BIG.encode("gb18030"))

    conn = yb.connect_index()
    res = await crawl.build(http_for(handler), conn, [series()])
    assert [(y, n >= 30) for _, y, n in res.ok] == [(2023, True), (2025, True)]
    assert res.missing == [("t", 2024)] and not res.failed
    first = len(seen)
    again = await crawl.build(http_for(handler), conn, [series()])  # 续跑：ok 与 missing 都跳过
    assert len(seen) == first and not again.ok


async def test_toc_falls_back_to_second_candidate_and_rejects_non_toc_pages():
    def handler(req):
        if str(req.url).endswith("left.htm"):
            return httpx.Response(404)
        return httpx.Response(200, content=BIG.encode("utf-8"))

    status, url, entries, _ = await crawl.crawl_edition(http_for(handler), series(), 2025)
    assert status == "ok" and url.endswith("lefte.htm") and len(entries) > 30

    status, _, _, msg = await crawl.crawl_edition(http_for(lambda r: httpx.Response(200, text="<html>错误页</html>")), series(), 2025)
    assert status == "error" and "不像年鉴目录页" in msg


async def test_blocked_site_gives_up_that_series_only():
    def handler(req):
        if "blocked.example" in str(req.url):
            return httpx.Response(403)
        return httpx.Response(200, content=BIG.encode("utf-8"))

    bad = series(id="bad", zh="被拒", base="https://blocked.example/{year}/")
    good = series(id="good", zh="正常")
    conn = yb.connect_index()
    res = await crawl.build(http_for(handler), conn, [bad, good])
    assert len(res.stopped) == 1 and "被拒" in res.stopped[0]
    assert {s for s, _, _ in res.ok} == {"good"}  # 一个站点拒绝不影响其他系列
    assert conn.execute("SELECT COUNT(*) FROM edition WHERE series='bad'").fetchone()[0] == 1  # 只试了一年就停


async def test_navigation_only_series_are_skipped():
    conn = yb.connect_index()
    res = await crawl.build(http_for(lambda r: pytest.fail("不该联网")), conn, [series(crawl=False)])
    assert not res.ok and not res.failed


def test_registry_is_consistent():
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
