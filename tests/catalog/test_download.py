"""受控下载的安全约束：只允许 http(s)、拒绝内网、重定向受限、体积上限、文件名净化与路径约束。"""

import hashlib

import httpx
import pytest

from datafinder.catalog import download as dl
from datafinder.catalog.adapters import AdapterError


def transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """测试里不真的解析域名：把 example.* 当公网，localhost/内网字面量照常识别。"""
    monkeypatch.setattr(
        dl,
        "_is_private",
        lambda host: (
            host in ("localhost", "intranet.local")
            or host.startswith(("10.", "127.", "192.168.", "169.254."))
        ),
    )


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://x.example/a.xls", "javascript:alert(1)", "http:///nohost"]
)
def test_only_http_and_https_with_a_host(url):
    with pytest.raises(AdapterError):
        dl.check_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/a",
        "http://127.0.0.1/a",
        "http://10.0.0.5/a",
        "http://192.168.1.1/a",
        "http://169.254.169.254/latest/meta-data",
        "http://intranet.local/a",
    ],
)
def test_private_loopback_and_metadata_addresses_are_refused(url):
    with pytest.raises(AdapterError, match="内网/本机"):
        dl.check_url(url)


def test_redirects_may_stay_on_the_same_registered_domain_only():
    assert dl.check_url("https://img.stats.example.cn/a.xls", origin_host="www.stats.example.cn")
    with pytest.raises(AdapterError, match="其他站点"):
        dl.check_url("https://evil.example.org/a.xls", origin_host="www.stats.example.cn")


def test_download_writes_file_with_sanitized_name_inside_dest(tmp_path):
    body = b"xls-bytes"
    path, size = dl.download_file(
        "https://stats.example.cn/files/%E8%A1%A8 1?x=1.xls",
        tmp_path,
        hint="rid",
        transport=transport(lambda r: httpx.Response(200, content=body)),
    )
    assert size == len(body) and path.read_bytes() == body and path.parent == tmp_path
    assert " " not in path.name and "/" not in path.name and not list(tmp_path.glob("*.part"))


def test_path_traversal_in_url_cannot_escape_dest_dir(tmp_path):
    path, _ = dl.download_file(
        "https://stats.example.cn/..%2F..%2Fetc%2Fpasswd",
        tmp_path / "d",
        hint="x",
        transport=transport(lambda r: httpx.Response(200, content=b"x")),
    )
    assert (tmp_path / "d") in path.parents


def test_filename_is_stable_per_resource_and_distinct_across_resources():
    a = dl.safe_filename("https://x.example/a/14-08.jpg", "res-1")
    assert a == dl.safe_filename("https://x.example/a/14-08.jpg", "res-1")
    assert a != dl.safe_filename("https://x.example/a/14-08.jpg", "res-2") and a.endswith(".jpg")
    assert hashlib.sha1(b"res-1").hexdigest()[:8] in a


def test_follows_same_domain_redirect_but_refuses_cross_site_and_loops(tmp_path):
    def same(req):
        if req.url.path == "/a":
            return httpx.Response(302, headers={"location": "https://cdn.stats.example.cn/b"})
        return httpx.Response(200, content=b"ok")

    path, _ = dl.download_file(
        "https://www.stats.example.cn/a", tmp_path, hint="r", transport=transport(same)
    )
    assert path.read_bytes() == b"ok"
    cross = lambda r: httpx.Response(302, headers={"location": "https://evil.example.org/x"})  # noqa: E731
    with pytest.raises(AdapterError, match="其他站点"):
        dl.download_file(
            "https://www.stats.example.cn/a", tmp_path, hint="r", transport=transport(cross)
        )
    loop = lambda r: httpx.Response(302, headers={"location": "https://www.stats.example.cn/a"})  # noqa: E731
    with pytest.raises(AdapterError, match="重定向次数过多"):
        dl.download_file(
            "https://www.stats.example.cn/a", tmp_path, hint="r", transport=transport(loop)
        )


def test_oversized_download_is_aborted_and_leaves_no_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "MAX_BYTES", 10)
    with pytest.raises(AdapterError, match="上限"):
        dl.download_file(
            "https://x.example/a.zip",
            tmp_path,
            hint="r",
            transport=transport(lambda r: httpx.Response(200, content=b"x" * 100)),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("status", "msg"),
    [(403, "拒绝了自动下载"), (412, "拒绝了自动下载"), (404, "HTTP 404"), (500, "HTTP 500")],
)
def test_http_errors_become_actionable_messages(tmp_path, status, msg):
    with pytest.raises(AdapterError, match=msg):
        dl.download_file(
            "https://x.example/a.xls",
            tmp_path,
            hint="r",
            transport=transport(lambda r: httpx.Response(status)),
        )
