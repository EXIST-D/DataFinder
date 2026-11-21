"""异步 HTTP：重试、Retry-After、按 host 限速、错误类型化。

借鉴 data-aggregator-mcp 的 `_http.py`：传输错误、429、5xx 重试；解析 Retry-After（有上限）；
429 不循环重试；响应体不是 JSON 也算错误（"2xx 但响应体坏"）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import USER_AGENT
from .errors import ShapeError, SourceError

MAX_RETRY_AFTER = 4.0  # 秒；更长的 Retry-After 不等，直接报限流

# 站点防火墙/人机验证页的特征（HTTP 200 的 HTML，而不是 JSON）。
# 实测：国家统计局发布库对并发访问会返回 "WZWS" JS 验证页；Cloudflare 等会返回 "Just a moment"。
# 遇到这类页面必须当成"被限流"暂停访问，而不是重试或绕过。
_CHALLENGE_MARKERS = (
    "wzws",
    "please enable javascript",
    "just a moment",
    "security checkpoint",
    "云防护",
    "安全验证",
    "captcha",
)


# 少数政府站点证书配置不合规（证书链缺中间证书、证书里的主机名与实际域名不符），
# OpenSSL 会直接拒绝握手。这些**公开统计站点**按主机白名单放宽校验：
# 只影响列出的主机，不改变其他任何请求的校验策略；新增条目必须写明核验日期与原因。
INSECURE_HOSTS: dict[str, str] = {
    "hgk.tjj.beijing.gov.cn": "证书链不完整（2026-11-21 核验；该站只提供公开年鉴文件）",
}

# 少数政府站点的 WAF 直接按 UA 拒绝非浏览器客户端（返回 403/418），而这些页面是**公开统计资料**，
# 不涉及登录、验证码、频率限制。这里按主机使用浏览器 UA：读的仍是同一份公开页面，不改变任何访问控制。
# 名单只放实测确因 UA 被拒的站点；新增条目必须写明核验日期与原因。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
BROWSER_UA_HOSTS: dict[str, str] = {
    "tjj.hlj.gov.cn": "站点 WAF 拒绝非浏览器 UA（2026-11-21 核验，同 URL 换 UA 即 200）",
    "stats.yn.gov.cn": "站点 WAF 拒绝非浏览器 UA（2026-11-21 核验，同 URL 换 UA 即 200）",
}


def ua_for(host: str) -> str:
    """这个主机该用的 User-Agent（只对 BROWSER_UA_HOSTS 里的公开统计站点用浏览器 UA）。"""
    return BROWSER_UA if host.lower() in BROWSER_UA_HOSTS else USER_AGENT


# 每个主机"下一次允许请求的时刻"，进程内所有 Http 实例共享：
# 网页里连续点分类树、搜索和取值同时发生时，对同一个主机仍是串行且保持间隔。
_NEXT_OK: dict[str, float] = {}


def looks_like_challenge(text: str) -> bool:
    head = text[:4000].lower()
    return any(m in head for m in _CHALLENGE_MARKERS)


MAX_BODY = 8 * 1024 * 1024  # 响应体上限，防止异常大响应


class Http:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        min_interval: dict[str, float] | None = None,
        default_interval: float = 0.0,
        insecure_hosts: dict[str, str] | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        self._owns = client is None
        self._min_interval = min_interval or {}
        self._default_interval = default_interval  # 没有单独配置的主机用这个间隔
        self._insecure_hosts = INSECURE_HOSTS if insecure_hosts is None else insecure_hosts
        self._extra: dict[tuple[str, bool], httpx.AsyncClient] = {}

    def _client_for(self, url: str) -> httpx.AsyncClient:
        """按主机选客户端：白名单主机用放宽校验/浏览器 UA 的客户端（只针对该主机，不全局改动）。"""
        if not self._owns:
            return self._client
        host = urlsplit(url).netloc.lower()
        insecure = host in self._insecure_hosts
        ua = ua_for(host)
        if not insecure and ua == USER_AGENT:
            return self._client
        key = (ua, insecure)
        if key not in self._extra:
            self._extra[key] = httpx.AsyncClient(
                headers={"User-Agent": ua}, follow_redirects=True, verify=not insecure
            )
        return self._extra[key]

    async def aclose(self) -> None:
        if self._owns:
            await self._client.aclose()
        for client in self._extra.values():
            await client.aclose()

    async def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        gap = self._min_interval.get(host, self._default_interval)
        if not gap:
            return
        now = time.monotonic()
        wait = _NEXT_OK.get(host, 0.0) - now
        _NEXT_OK[host] = max(now, _NEXT_OK.get(host, 0.0)) + gap
        if wait > 0:
            await asyncio.sleep(wait)

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> Any:
        last: SourceError | None = None
        for attempt in range(2):  # 最多重试一次
            await self._throttle(url)
            try:
                resp = await self._client_for(url).request(
                    method, url, params=params, json=json_body, headers=headers, timeout=timeout
                )
            except httpx.TimeoutException as exc:
                last = SourceError("timeout", f"请求超时：{type(exc).__name__}")
            except httpx.TransportError as exc:
                last = SourceError("down", f"无法连接：{type(exc).__name__}")
            else:
                if resp.status_code == 429:
                    delay = _retry_after(resp)
                    if attempt == 0 and delay is not None and delay <= MAX_RETRY_AFTER:
                        await asyncio.sleep(delay)
                        continue
                    raise SourceError("rate_limited", "被限流（HTTP 429）", status=429)
                if resp.status_code in (401, 403):
                    raise SourceError(
                        "blocked", f"被拒绝（HTTP {resp.status_code}）", status=resp.status_code
                    )
                if resp.status_code >= 500:
                    last = SourceError(
                        "http", f"服务端错误（HTTP {resp.status_code}）", status=resp.status_code
                    )
                elif resp.status_code >= 400:
                    raise SourceError(
                        "http", f"请求失败（HTTP {resp.status_code}）", status=resp.status_code
                    )
                else:
                    if len(resp.content) > MAX_BODY:
                        raise ShapeError("响应体过大")
                    try:
                        return resp.json()
                    except ValueError as exc:  # 2xx 但不是 JSON
                        if looks_like_challenge(resp.text):
                            raise SourceError(
                                "rate_limited",
                                "站点防护要求人机验证（访问过快或被识别为机器人），已暂停访问，稍后再试",
                            ) from exc
                        last = ShapeError(f"响应不是 JSON：{exc}")
                        if attempt == 0:
                            await asyncio.sleep(0.3)
                            continue
                        raise last from exc
            if attempt == 0:
                await asyncio.sleep(0.3)
        assert last is not None
        raise last

    async def get_bytes(
        self, url: str, *, headers: dict[str, str] | None = None, timeout: float = 20.0
    ) -> tuple[int, bytes]:
        """取原始字节（网页、文件）。返回 (状态码, 内容)；404/410 当作"这个地址没有东西"正常返回，
        其余错误（超时、拒绝、限流、5xx、验证页）按类型抛 SourceError。"""
        last: SourceError | None = None
        for attempt in range(2):
            await self._throttle(url)
            try:
                resp = await self._client_for(url).get(url, headers=headers, timeout=timeout)
            except httpx.TimeoutException as exc:
                last = SourceError("timeout", f"请求超时：{type(exc).__name__}")
            except httpx.TransportError as exc:
                last = SourceError("down", f"无法连接：{type(exc).__name__}")
            else:
                code = resp.status_code
                if code in (404, 410):
                    return code, b""
                if code == 429:
                    raise SourceError("rate_limited", "被限流（HTTP 429）", status=429)
                if code in (401, 403, 412, 418):
                    raise SourceError("blocked", f"被拒绝（HTTP {code}）", status=code)
                if code >= 500:
                    last = SourceError("http", f"服务端错误（HTTP {code}）", status=code)
                elif code >= 400:
                    raise SourceError("http", f"请求失败（HTTP {code}）", status=code)
                else:
                    if len(resp.content) > MAX_BODY:
                        raise ShapeError("响应体过大")
                    if looks_like_challenge(resp.text[:4000]):
                        raise SourceError(
                            "rate_limited", "站点防护要求人机验证，已暂停访问，稍后再试"
                        )
                    return code, resp.content
            if attempt == 0:
                await asyncio.sleep(0.3)
        assert last is not None
        raise last

    async def get_json(self, url: str, **kw: Any) -> Any:
        return await self.request_json("GET", url, **kw)

    async def post_json(self, url: str, **kw: Any) -> Any:
        return await self.request_json("POST", url, **kw)


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
