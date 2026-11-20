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
