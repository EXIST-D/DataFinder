"""运行时路径与常量。"""

from __future__ import annotations

import os
from pathlib import Path

# 统一 User-Agent，便于来源方识别（后续可加上项目地址）
USER_AGENT = "DataFinder/0.0.1 (+https://github.com/EXIST-D/DataFinder)"


def data_dir() -> Path:
    """本地数据目录（SQLite 门户库、缓存等）。可用 DATAFINDER_HOME 覆盖。"""
    override = os.environ.get("DATAFINDER_HOME")
    return Path(override).expanduser() if override else Path.home() / ".datafinder"
