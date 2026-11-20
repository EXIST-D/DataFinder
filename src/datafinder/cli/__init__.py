"""命令行入口：`datafinder`（别名 `dfd`）。

- app    主命令（体检、同步、检索、网页等）
- data   找数方案、国家统计局取数、描述统计、MCP
"""

from .app import app, main

__all__ = ["app", "main"]
