"""受控下载：只下载目录里登记过的文件直链，写入受控目录，附清单。

安全约束（不是可选项）：
- 只接受目录里这个资源登记的 URL，不接受任意 URL；
- 只允许 http/https；主机不得是私网/回环/链路本地地址（含解析结果）；重定向最多 3 跳，且必须留在同一主机或同一注册域；
- 体积上限 200MB，超时 60 秒；文件名净化，写入位置必须在目标目录内；
- 不自动登录、不绕过验证码；未知许可不代表可随意使用（清单里如实写）。
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from ..config import USER_AGENT
from .adapters import AdapterError

MAX_BYTES = 200 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 60.0
_SAFE = re.compile(r"[^\w\u4e00-\u9fff.-]+")


def _registered_domain(host: str) -> str:
    return ".".join(host.split(".")[-2:])


def _is_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False  # 解析失败交给请求本身报错
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def check_url(url: str, *, origin_host: str | None = None) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise AdapterError(f"只允许 http/https 链接：{url}")
    host = parts.hostname.lower()
    if (
        origin_host
        and host != origin_host
        and _registered_domain(host) != _registered_domain(origin_host)
    ):
        raise AdapterError(f"重定向到了其他站点（{host}），已拒绝")
    if _is_private(host):
        raise AdapterError(f"拒绝访问内网/本机地址：{host}")
    return host


def safe_filename(url: str, hint: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or "download"
    stem, dot, ext = name.rpartition(".")
    base = _SAFE.sub("_", stem or name).strip("_")[:60] or "file"
    tag = hashlib.sha1(hint.encode()).hexdigest()[:8]
    return f"{base}-{tag}{dot}{_SAFE.sub('', ext)[:8]}" if dot else f"{base}-{tag}"


def download_file(
    url: str, dest_dir: Path, *, hint: str, transport: httpx.BaseTransport | None = None
) -> tuple[Path, int]:
    """下载到 dest_dir；返回 (文件路径, 字节数)。"""
    origin = check_url(url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = (dest_dir / safe_filename(url, hint)).resolve()
    if dest_dir.resolve() not in target.parents:
        raise AdapterError("目标路径越界")
    current = url
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=False,
        transport=transport,
    ) as c:
        for _ in range(MAX_REDIRECTS + 1):
            with c.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    current = str(httpx.URL(current).join(r.headers.get("location", "")))
                    check_url(current, origin_host=origin)
                    continue
                if r.status_code in (401, 403, 412, 418):
                    raise AdapterError(
                        f"站点拒绝了自动下载（HTTP {r.status_code}）：请用浏览器打开原站页面"
                    )
                if r.status_code >= 400:
                    raise AdapterError(f"下载失败（HTTP {r.status_code}）")
                size = 0
                tmp = target.with_suffix(target.suffix + ".part")
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            tmp.unlink(missing_ok=True)
                            raise AdapterError(f"文件超过 {MAX_BYTES // (1 << 20)}MB 上限，已中止")
                        f.write(chunk)
                tmp.replace(target)
                return target, size
    raise AdapterError("重定向次数过多，已中止")
