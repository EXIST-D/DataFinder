import pytest

from datafinder.portals.sync import Portal, write_db
from datafinder.store import connect


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """所有测试都用临时数据目录，绝不碰用户真实的 ~/.datafinder。"""
    monkeypatch.setenv("DATAFINDER_HOME", str(tmp_path / "home"))


def P(id, name, **kw):
    kw.setdefault("source", "dateno")
    kw.setdefault("url", f"https://{id}.example.org")
    return Portal(id=id, name=name, **kw)


@pytest.fixture
def portals():
    conn = connect()
    write_db(
        conn,
        [
            P("miit", "MIIT", name_zh="工业和信息化部", country="CN", owner_type="Central government",
              industries=["industry"], labels=["ministry"], featured=True, is_national=True, verify="ok"),
            P("hebei", "Hebei Stats", name_zh="河北省统计局", country="CN", region="CN-HE", owner_type="Regional government",
              catalog_type="Indicators catalog", industries=["statistics"], labels=["stats_bureau"], verify="unverified"),
            P("us", "US Industry", country="US", owner_type="Central government", industries=["industry"]),
        ],
        meta={},
    )
    conn.close()
