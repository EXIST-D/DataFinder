"""读取包内数据文件（词表、行业、地区、精选门户）。"""

from __future__ import annotations

from importlib.resources import files
from typing import Any

import yaml


def load_yaml(*parts: str) -> Any:
    path = files("datafinder").joinpath("data", *parts)
    return yaml.safe_load(path.read_text(encoding="utf-8"))
