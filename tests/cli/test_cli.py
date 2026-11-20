import pytest
from typer.testing import CliRunner

from datafinder import __version__
from datafinder.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_doctor_offline_runs_without_network():
    result = runner.invoke(app, ["doctor", "--offline"])
    assert result.exit_code == 0
    assert "Python" in result.output


def test_doctor_finds_dataseek_the_same_way_search_does(monkeypatch):
    # uv tool 安装时 dsk 在工具自己的环境里、不在 PATH 上；doctor 必须用与搜索相同的查找函数
    from datafinder import doctor

    monkeypatch.setattr(doctor, "find_executable", lambda: "/somewhere/venv/bin/dsk")
    monkeypatch.setattr(
        doctor.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "dataseek 0.10.1\n"})()
    )
    check = doctor.check_dataseek()
    assert check.status == "ok" and "0.10.1" in check.detail


def test_doctor_reports_missing_dataseek_as_optional(monkeypatch):
    from datafinder import doctor

    monkeypatch.setattr(doctor, "find_executable", lambda: None)
    assert doctor.check_dataseek().status == "warn"


# ───────────── 资源目录命令 ─────────────

IND = "nbs:indicator:6/c1/i1"


@pytest.fixture
def ready_catalog():
    from datafinder.catalog import Catalog

    from ..catalog.conftest import build

    build(Catalog.open()).close()


def test_command_set_matches_the_capability_layers():
    names = {c.name or c.callback.__name__ for c in app.registered_commands} | {
        g.name for g in app.registered_groups
    }
    assert {
        "search",
        "inspect",
        "facets",
        "sources",
        "get",
        "plan",
        "describe",
        "mcp",
        "index",
        "datasets",
    } <= names
    assert not {"nbs", "yearbook"} & names  # 领域子命令已并入通用命令


def test_search_without_catalog_explains_how_to_build_it(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFINDER_HOME", str(tmp_path / "empty"))
    result = runner.invoke(app, ["search", "工业"])
    assert (
        result.exit_code == 1
        and "INDEX_NOT_READY" in result.output
        and "index build" in result.output
    )


def test_search_filters_and_json_output(ready_catalog):
    import json

    result = runner.invoke(app, ["search", "工业增加值", "-r", "河北", "-k", "indicator", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["results"][0]["resource_id"] == IND and data["results"][0]["delivery"] == "generate"
    text = runner.invoke(
        app, ["search", "规模以上工业增加值", "-r", "山东", "-d", "download"]
    ).output
    assert "可下载" in text and "yb.sd:table:2025/14-08" in text


def test_search_reports_bad_facets_with_code_and_next_step(ready_catalog):
    result = runner.invoke(app, ["search", "工业", "-t", "不存在的主题"])
    assert (
        result.exit_code == 1 and "INVALID_FACET_ID" in result.output and "facets" in result.output
    )


def test_inspect_and_facets_commands(ready_catalog):
    out = runner.invoke(app, ["inspect", IND]).output
    assert "分省年度数据" in out and "怎么获取" in out and "fetch" in out
    assert (
        "nbs:indicator:6/c1/i1" in runner.invoke(app, ["inspect", "nbs:dataset:6/c1", "-c"]).output
    )
    assert runner.invoke(app, ["inspect", "nbs:nope"]).exit_code == 1
    assert "cnstat.industry" in runner.invoke(app, ["facets"]).output


def test_get_only_previews_until_yes(ready_catalog):
    from datafinder.tables import exports_dir

    out = runner.invoke(app, ["get", "yb.sd:table:2025/14-08"]).output
    assert "预览" in out and "OCR" in out and "--yes" in out
    assert not list(exports_dir().glob("*"))
    assert "只有链接" in runner.invoke(app, ["get", "yb.sd:series:CN-SD"]).output


def test_get_yes_downloads_via_the_guarded_downloader(ready_catalog, monkeypatch):
    def fake(url, dest_dir, *, hint, transport=None):
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "t.jpg").write_bytes(b"jpg")
        return dest_dir / "t.jpg", 3

    monkeypatch.setattr("datafinder.service.acquire.download_file", fake)
    out = runner.invoke(app, ["get", "yb.sd:table:2025/14-08", "--yes"]).output
    assert "已保存" in out and "t.jpg" in out


def test_index_status_and_migrate_without_legacy(ready_catalog):
    out = runner.invoke(app, ["index", "status"]).output
    assert "资源目录：9 条" in out
    assert "没有找到旧索引" in runner.invoke(app, ["index", "migrate"]).output


def test_index_build_rejects_bad_params_and_unknown_adapters():
    r = runner.invoke(app, ["index", "build", "nbs", "--opt", "db=99"])
    assert r.exit_code == 1 and "未知数据库代码" in r.output
    assert runner.invoke(app, ["index", "build", "nbs", "--opt", "oops"]).exit_code != 0
    assert runner.invoke(app, ["index", "build", "nope"]).exit_code == 1


def test_mcp_print_config_and_plan_json(ready_catalog, portals):
    import json

    cfg = json.loads(runner.invoke(app, ["mcp", "--print-config"]).output)
    assert cfg["mcpServers"]["datafinder"]["args"] == ["mcp"]
    plan = json.loads(runner.invoke(app, ["plan", "河北省工业增加值", "--json"]).output)
    assert plan["sources"]["indicators"]["items"][0]["resource_id"] == IND
