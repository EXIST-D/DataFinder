"""中英词表与中文最长匹配分词。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from ..resources import load_yaml
from .tokenize import CJK_CHARS, normalize

Kind = Literal["word", "weak", "unknown", "latin"]

_PIECE = re.compile(rf"[a-z0-9][a-z0-9.+_-]*|[{CJK_CHARS}]+")
_IS_CJK_PIECE = re.compile(rf"^[{CJK_CHARS}]")


@dataclass(frozen=True)
class Lexicon:
    zh2en: dict[str, tuple[str, ...]]
    weak: frozenset[str]
    stop: frozenset[str]
    dimension: frozenset[str]
    max_len: int

    def zh_keys_for_en(self, word: str) -> list[str]:
        """英文词 → 所有把它当首选说法的中文词（gdp → 国内生产总值），给英文概念补中文说法。"""
        word = normalize(word)
        return [
            zh for zh, ens in self.zh2en.items() if not zh.isascii() and normalize(ens[0]) == word
        ]

    def zh_for_en(self, word: str) -> str | None:
        """英文词 → 对应的中文词（如 gdp → 国内生产总值），给中文来源构造检索词用。"""
        word = normalize(word)
        best: str | None = None
        for zh, ens in self.zh2en.items():
            if zh.isascii() or word not in (normalize(e) for e in ens):
                continue
            if normalize(ens[0]) == word:
                return zh
            best = best or zh
        return best


@dataclass(frozen=True)
class Piece:
    text: str
    kind: Kind


@lru_cache(maxsize=1)
def load_lexicon() -> Lexicon:
    raw = load_yaml("lexicon.zh-en.yaml")
    weak = frozenset(normalize(w) for w in raw.pop("weak", []))
    stop = frozenset(normalize(w) for w in raw.pop("stop", []))
    dimension = frozenset(normalize(w) for w in raw.pop("dimension", []))
    zh2en = {normalize(k): tuple(str(v) for v in vals) for k, vals in raw.items()}
    longest = max([len(k) for k in zh2en] + [len(w) for w in weak | stop] + [1])
    return Lexicon(zh2en=zh2en, weak=weak, stop=stop, dimension=dimension, max_len=longest)


def segment(text: str, lex: Lexicon | None = None) -> list[Piece]:
    """把查询切成 词表词 / 泛化词 / 未登录中文串 / 拉丁词。停用词直接丢弃。

    中文用最长匹配；未命中的连续汉字合并成一个"未登录串"（单字串丢弃，噪声太大）。
    """
    lex = lex or load_lexicon()
    pieces: list[Piece] = []
    for m in _PIECE.finditer(normalize(text)):
        run = m.group()
        if not _IS_CJK_PIECE.match(run):
            pieces.append(Piece(run, "latin"))
            continue
        unknown = ""

        def flush() -> None:
            nonlocal unknown
            if len(unknown) >= 2:
                pieces.append(Piece(unknown, "unknown"))
            unknown = ""

        i = 0
        while i < len(run):
            hit: tuple[str, Kind | None] | None = None
            for size in range(min(lex.max_len, len(run) - i), 0, -1):
                word = run[i : i + size]
                if word in lex.stop:
                    hit = (word, None)
                elif word in lex.weak:
                    hit = (word, "weak")
                elif word in lex.zh2en:
                    hit = (word, "word")
                if hit:
                    break
            if hit is None:
                unknown += run[i]
                i += 1
                continue
            flush()
            word, kind = hit
            if kind is not None:
                pieces.append(Piece(word, kind))
            i += len(word)
        flush()
    return pieces
