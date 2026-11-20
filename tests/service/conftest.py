import pytest

from datafinder.catalog import Catalog

from ..catalog.conftest import build


@pytest.fixture
def ready():
    """在默认数据目录（测试里是临时目录）建好合成目录。"""
    cat = build(Catalog.open())
    cat.close()
