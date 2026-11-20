"""统一分词：拉丁词 + 中日韩二元组（bigram）。

为什么不用 SQLite FTS5 自带的 trigram：它对 2 字词（人口、气象）匹配不到结果。
这里自己切分，**文档和查询用同一个函数**，所以两字词、三字词都能命中，零依赖。

规则：
- NFKC 归一并转小写（全角字母数字也归一）。
- 拉丁/数字：按 [a-z0-9]+ 切词，做极简复数还原（statistics→statistic）。
- CJK 连续串：切成相邻两字组合（"人口普查"→人口 口普 普查）；单字串保留单字。
"""

from __future__ import annotations

import re
import unicodedata

CJK_CHARS = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
_TOKEN = re.compile(rf"[a-z0-9]+|[{CJK_CHARS}]+")
_IS_CJK = re.compile(rf"[{CJK_CHARS}]")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def has_cjk(text: str) -> bool:
    return bool(_IS_CJK.search(text))


def _stem(word: str) -> str:
    """极简复数还原。只要文档与查询一致就行，不追求语言学正确。"""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]  # census / analysis / bus 这类词保持原样
    return word


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for m in _TOKEN.finditer(normalize(text)):
        piece = m.group()
        if _IS_CJK.match(piece):
            if len(piece) == 1:
                tokens.append(piece)
            else:
                tokens.extend(piece[i : i + 2] for i in range(len(piece) - 1))
        else:
            tokens.append(_stem(piece))
    return tokens


def token_text(text: str) -> str:
    """分词后用空格连接，供写入 FTS 与做子串匹配。"""
    return " ".join(tokenize(text))
