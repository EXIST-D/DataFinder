"""小工具。"""

from __future__ import annotations

from urllib.parse import urlsplit


def normalize_url(url: str) -> str:
    """规范化 URL 用于去重/匹配：去 scheme、www.、fragment、末尾斜杠，host 小写。"""
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"
