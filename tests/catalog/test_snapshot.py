"""目录快照：制作、安装、校验、版本与新旧保护、https 下载。"""

import gzip
import json
import sqlite3

import httpx
import pytest

from datafinder.catalog import Catalog
from datafinder.catalog.schema import db_path
from datafinder.catalog.snapshot import (
    DB_NAME,
    MANIFEST_NAME,
    SnapshotError,
    build_snapshot,
    install_snapshot,
    local_state,
)

from .conftest import build


@pytest.fixture
def snap(tmp_path):
    cat = build(Catalog.open())
    n = cat.stats()["resources"]
    cat.close()
    out = tmp_path / "snap"
    manifest = build_snapshot(out)
    assert manifest["resources"] == n
    # 换成一个空的数据目录，模拟新用户
    db_path().unlink()
    return out, manifest


def test_roundtrip_from_directory(snap):
    out, manifest = snap
    assert local_state() is None
    r = install_snapshot(str(out))
    assert r["backup"] is None and r["manifest"]["sha256"] == manifest["sha256"]
    with Catalog.open() as cat:
        assert cat.stats()["resources"] == manifest["resources"]
        assert cat.conn.execute("PRAGMA user_version").fetchone()[0] == 1
        # 全文索引随库一起带过来，检索可用
        assert cat.conn.execute("SELECT COUNT(*) FROM resources_fts").fetchone()[0] > 0


def test_existing_catalog_is_backed_up(snap):
    out, _ = snap
    install_snapshot(str(out))
    r = install_snapshot(str(out), force=True)
    assert r["backup"] and r["backup"].endswith("catalog.db.bak")


def test_refuses_to_overwrite_newer_local(snap):
    out, manifest = snap
    manifest["created_at"] = "2000-01-01T00:00:00+00:00"
    (out / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    install_snapshot(str(out), force=True)
    with pytest.raises(SnapshotError, match="更新"):
        install_snapshot(str(out))
    install_snapshot(str(out), force=True)


def test_tampered_file_is_rejected_and_local_untouched(snap):
    out, _ = snap
    install_snapshot(str(out))
    before = db_path().read_bytes()
    raw = bytearray((out / DB_NAME).read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    (out / DB_NAME).write_bytes(bytes(raw))
    with pytest.raises(SnapshotError, match="校验和"):
        install_snapshot(str(out), force=True)
    assert db_path().read_bytes() == before
    assert not list(db_path().parent.glob("*.part*")) and not list(db_path().parent.glob("*.new"))


def test_schema_version_mismatch(snap):
    out, manifest = snap
    manifest["schema_version"] = 99
    (out / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SnapshotError, match="库版本"):
        install_snapshot(str(out))


def test_content_must_match_manifest(snap, tmp_path):
    out, manifest = snap
    bad = tmp_path / "bad.db"
    conn = sqlite3.connect(bad)
    conn.executescript("CREATE TABLE resources(source_id, indexed_at); PRAGMA user_version = 1;")
    conn.commit()
    conn.close()
    gz = out / DB_NAME
    gz.write_bytes(gzip.compress(bad.read_bytes()))
    import hashlib

    manifest["sha256"] = hashlib.sha256(gz.read_bytes()).hexdigest()
    (out / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SnapshotError, match="与清单不符"):
        install_snapshot(str(out))


def test_download_over_https_with_redirect(snap):
    out, manifest = snap
    files = {
        DB_NAME: (out / DB_NAME).read_bytes(),
        MANIFEST_NAME: (out / MANIFEST_NAME).read_bytes(),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if request.url.host == "cdn.example.invalid":
            return httpx.Response(200, content=files[name])
        return httpx.Response(302, headers={"location": f"https://cdn.example.invalid/x/{name}"})

    msgs: list[str] = []
    r = install_snapshot(
        "https://example.invalid/rel",
        transport=httpx.MockTransport(handler),
        progress=msgs.append,
    )
    assert r["manifest"]["resources"] == manifest["resources"]
    assert local_state()["resources"] == manifest["resources"]


def test_http_and_missing_release(snap):
    with pytest.raises(SnapshotError, match="https"):
        install_snapshot("http://example.invalid/rel")
    with pytest.raises(SnapshotError, match="还没有发布"):
        install_snapshot(
            "https://example.invalid/rel",
            transport=httpx.MockTransport(lambda r: httpx.Response(404)),
        )


def test_empty_catalog_cannot_be_snapshotted(tmp_path):
    Catalog.open().close()
    with pytest.raises(SnapshotError):
        build_snapshot(tmp_path / "x")
