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
