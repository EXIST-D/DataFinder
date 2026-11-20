import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """所有测试都用临时数据目录，绝不碰用户真实的 ~/.datafinder。"""
    monkeypatch.setenv("DATAFINDER_HOME", str(tmp_path / "home"))
