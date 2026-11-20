import httpx
import pytest

from datafinder.engine.cache import Cache
from datafinder.engine.errors import SourceError
from datafinder.engine.http import Http, looks_like_challenge

WZWS_PAGE = (
    "<!DOCTYPE HTML><html><body><noscript><h1><strong>Please enable JavaScript and refresh"
    "the page.</strong></h1></noscript><script>var c='/WZWSREL2RnL3dl';</script></body></html>"
)


def make(handler):
    return Http(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_challenge_detection():
    assert looks_like_challenge(WZWS_PAGE)
    assert looks_like_challenge("<title>Just a moment...</title>")
    assert looks_like_challenge("<h1>拒绝执行 | 云防护</h1>")
    assert not looks_like_challenge("<html><body>维护中</body></html>")


async def test_waf_challenge_page_is_rate_limited_not_a_shape_error():
    http = make(
        lambda req: httpx.Response(200, text=WZWS_PAGE, headers={"content-type": "text/html"})
    )
    with pytest.raises(SourceError) as e:
        await http.get_json("https://data.stats.gov.cn/x")
    assert e.value.kind == "rate_limited" and e.value.is_outage  # 暂停访问，不重试、不绕过


async def test_waf_challenge_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, text=WZWS_PAGE)

    with pytest.raises(SourceError):
        await make(handler).get_json("https://data.stats.gov.cn/x")
    assert len(calls) == 1


async def test_plain_html_error_is_still_a_shape_error():
    from datafinder.engine.errors import ShapeError

    with pytest.raises(ShapeError):
        await make(lambda req: httpx.Response(200, text="<html>maintenance</html>")).get_json(
            "https://x.org/a"
        )


def test_kv_cache_respects_ttl(tmp_path):
    c = Cache(tmp_path / "c.db")
    c.kv_put("k", {"a": [1, 2]})
    assert c.kv_get("k", max_age=60) == {"a": [1, 2]}
    assert c.kv_get("k", max_age=-1) is None  # 过期
    assert c.kv_get("missing", max_age=60) is None
    c.close()
