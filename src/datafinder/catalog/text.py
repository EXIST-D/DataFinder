"""目录通用的文本规范化（跨领域共用）。"""

from __future__ import annotations

import re

_PAREN = re.compile(r"[（(][^（）()]*[)）]")
# 标题里的年份：'2010-2024年规模以上工业增加值'、'……(2024年底)'、'2019年' 等，跨版次对齐时不应算作区别
_YEARS = re.compile(r"\d{4}(?:\s*[-—~～至]\s*\d{4})?\s*年(?:底|末|度)?")
_PUNCT = re.compile(r"[\s\u3000，,、:：]+")


def norm_title(title: str) -> str:
    """同一张表在不同版次里标题会带不同年份/括号说明：去掉它们，得到可归并的键。"""
    return _PUNCT.sub("", _YEARS.sub("", _PAREN.sub("", title)))


_T2S = None


def to_simplified(text: str) -> str:
    """繁体 → 简体（港澳台来源的标题、维度名统一成简体，才能被简体检索词命中）。"""
    global _T2S
    if _T2S is None:
        from opencc import OpenCC

        _T2S = OpenCC("t2s")
    return _T2S.convert(text)
