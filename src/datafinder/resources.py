"""读取包内数据文件（词表、行业、地区、精选门户；领域包有自己的 data/ 目录）。"""

from __future__ import annotations

from importlib.resources import files
from typing import Any

import yaml


def load_package_yaml(package: str, *parts: str) -> Any:
    """读取某个包的 data/ 目录下的 YAML。核心用 `load_yaml`；领域包用自己的包名。"""
    path = files(package).joinpath("data", *parts)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_yaml(*parts: str) -> Any:
    return load_package_yaml("datafinder", *parts)
