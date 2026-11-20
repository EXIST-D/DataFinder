"""领域包加载器。

一个领域包 = `datafinder/domains/<domain_id>/` 目录：自己的连接器、分类词典、术语词表、选题集，
并在 `plugin.py` 里向核心注册（来源协议、术语词表、限速等）。

**分层规则（有架构测试守护）**：核心代码不得直接 import 某个具体领域包的子模块，
只能通过本加载器（`load_all` / `loaded`）；领域包可以依赖核心，反过来不行。
"""

from __future__ import annotations

import importlib

# 已安装的领域包。新增领域：建目录 + plugin.py，再在这里加一行。
DOMAINS: tuple[str, ...] = ("official_statistics_cn",)

_loaded: list[str] = []


def load_all() -> list[str]:
    """导入各领域包的 plugin（幂等）。核心在需要领域扩展点之前调用。"""
    for domain in DOMAINS:
        if domain not in _loaded:
            _loaded.append(domain)  # 先登记，避免 plugin 内部再触发加载造成递归
            importlib.import_module(f"{__name__}.{domain}.plugin")
    return list(_loaded)


def data_packages() -> list[str]:
    """各领域包的包名（用来读它们各自的 data/ 目录）。"""
    return [f"{__name__}.{d}" for d in load_all()]
