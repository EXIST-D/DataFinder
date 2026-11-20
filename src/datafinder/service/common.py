"""服务层公共：打开目录（没建好时给出明确的下一步）。"""

from __future__ import annotations

from datafinder.catalog import Catalog

from .errors import ServiceError

NOT_READY = (
    "资源目录还没建立。请在终端运行 `datafinder index build`（抓取，可中断续跑），"
    "或 `datafinder index migrate`（把旧版本地索引一次性迁入）。"
)


def open_catalog(*, require_data: bool = True) -> Catalog:
    cat = Catalog.open()
    if require_data and cat.stats()["resources"] == 0:
        cat.close()
        raise ServiceError(NOT_READY, code="INDEX_NOT_READY", next_action="datafinder index build")
    return cat
