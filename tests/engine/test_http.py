import httpx
import pytest

from datafinder.engine.errors import ShapeError, SourceError
from datafinder.engine.http import Http


def make(handler):
    return Http(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_returns_json():
    http = make(lambda req: httpx.Response(200, json={"ok": 1}))
    assert await http.get_json("https://x.org/a") == {"ok": 1}


async def test_retries_once_on_5xx_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json={"ok": 1})

    assert await make(handler).get_json("https://x.org/a") == {"ok": 1}
    assert len(calls) == 2


async def test_persistent_5xx_is_outage():
    http = make(lambda req: httpx.Response(500))
    with pytest.raises(SourceError) as e:
        await http.get_json("https://x.org/a")
    assert e.value.is_outage


async def test_429_with_short_retry_after_retries_once():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"ok": 1})

    assert await make(handler).get_json("https://x.org/a") == {"ok": 1}


async def test_429_with_long_retry_after_does_not_wait_or_loop():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "120"})

    with pytest.raises(SourceError) as e:
        await make(handler).get_json("https://x.org/a")
    assert e.value.kind == "rate_limited" and e.value.is_outage
    assert len(calls) == 1


async def test_403_is_blocked():
    with pytest.raises(SourceError) as e:
        await make(lambda req: httpx.Response(403)).get_json("https://x.org/a")
    assert e.value.kind == "blocked"


async def test_404_is_not_outage():
    with pytest.raises(SourceError) as e:
        await make(lambda req: httpx.Response(404)).get_json("https://x.org/a")
    assert e.value.kind == "http" and not e.value.is_outage


async def test_2xx_with_non_json_body_is_shape_error():
    with pytest.raises(ShapeError):
        await make(lambda req: httpx.Response(200, text="<html>maintenance</html>")).get_json(
            "https://x.org/a"
        )


async def test_connection_error_is_outage():
    def handler(req):
        raise httpx.ConnectError("boom")

    with pytest.raises(SourceError) as e:
        await make(handler).get_json("https://x.org/a")
    assert e.value.kind == "down" and e.value.is_outage
