"""去重：身份键 + 并查集合并。对中文安全。

两条记录共享任一身份键即视为同一个数据集（传递性）：
1. `doi:`   DOI（含 aliases、藏在 URL 里的 DOI、concept DOI），已规范化
2. `url:`   规范化 URL（去 scheme、www、fragment、末尾斜杠、utm_*）
3. `title:` 规范化标题 + 发布方——**只跨来源合并**，同一来源内两条同名记录不合并

先到者字段优先，后到者只补缺。设计取自 Dataseek（`dedup.rs`），
区别：标题规范化用 NFKC + 去标点空白，**不用 `[^a-z0-9]`**（那会把所有中文标题碰成同一个键，
这正是 Mobus 的 bug）；长度阈值按字符数而不是字节数。
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit

from ..models import Dataset, Resource

_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"<>?#]+", re.I)
_PUNCT = re.compile(r"[\W_]+", re.UNICODE)
MIN_TITLE_CHARS = 8  # 标题太短（如 "Data"）不用标题键，避免误合并


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    m = _DOI_IN_TEXT.search(unicodedata.normalize("NFKC", value))
    return m.group().lower().rstrip("./") if m else None


def normalize_link(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    )
    return f"{host}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")


def normalize_title(title: str) -> str:
    return _PUNCT.sub("", unicodedata.normalize("NFKC", title).casefold())


def identity_keys(d: Dataset) -> tuple[list[str], list[str]]:
    """返回 (强键, 标题键)。标题键只允许跨来源合并。"""
    strong: list[str] = []
    for raw in [d.doi, *d.aliases, d.url]:
        doi = normalize_doi(raw)
        if doi:
            strong.append(f"doi:{doi}")
    if d.url:
        strong.append(f"url:{normalize_link(d.url)}")
    titles: list[str] = []
    norm = normalize_title(d.title)
    if len(norm) >= MIN_TITLE_CHARS:
        titles.append(f"title:{norm}|{normalize_title(d.publisher or '')}")
    return list(dict.fromkeys(strong)), titles


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)  # 小下标（先到者）为根


def _fill(base: Dataset, other: Dataset) -> None:
    """后到者只补缺：不覆盖已有字段。"""
    for name in ("description", "publisher", "doi", "license", "updated", "period", "size_bytes"):
        if getattr(base, name) in (None, "") and getattr(other, name) not in (None, ""):
            setattr(base, name, getattr(other, name))
    base.formats = list(dict.fromkeys([*base.formats, *other.formats]))
    seen = {r.url for r in base.resources}
    base.resources += [r for r in other.resources if r.url not in seen]
    base.aliases = list(dict.fromkeys([*base.aliases, *other.aliases]))
    for s in other.sources:
        if s not in base.sources:
            base.sources.append(s)


def merge(per_source: dict[str, list[Dataset]]) -> tuple[list[Dataset], list[dict[str, int]]]:
    """合并各来源的有序列表。返回 (合并后的数据集, 每个数据集在各来源的最佳名次 {来源: rank})。

    per_source 的迭代顺序即来源优先级（先到者字段优先）。
    """
    items: list[tuple[Dataset, int]] = []  # (数据集, 在其来源中的名次)
    for src, lst in per_source.items():
        for rank, d in enumerate(lst):
            d.source = src  # 来源适配器 id（排序时按它统计名次）
            if not d.sources:
                d.sources = [src]  # 展示用出处；Dataseek 会带回底层仓库名，保持不动
            items.append((d, rank))

    uf = _UnionFind(len(items))
    by_strong: dict[str, int] = {}
    by_title: dict[str, list[int]] = {}
    for idx, (d, _) in enumerate(items):
        strong, titles = identity_keys(d)
        for key in strong:
            if key in by_strong:
                uf.union(by_strong[key], idx)
            else:
                by_strong[key] = idx
        for key in titles:
            for other in by_title.get(key, []):
                if items[other][0].source != d.source:  # 标题键只跨来源
                    uf.union(other, idx)
            by_title.setdefault(key, []).append(idx)

    groups: dict[int, list[int]] = {}
    for idx in range(len(items)):
        groups.setdefault(uf.find(idx), []).append(idx)

    merged: list[Dataset] = []
    ranks: list[dict[str, int]] = []
    for root in sorted(groups):
        members = groups[root]
        base = items[members[0]][0]
        best: dict[str, int] = {}
        for idx in members:
            d, rank = items[idx]
            best[d.source] = min(rank, best.get(d.source, rank))
            if idx != members[0]:
                _fill(base, d)
        merged.append(base)
        ranks.append(best)
    return merged, ranks


__all__ = [
    "Resource",
    "identity_keys",
    "merge",
    "normalize_doi",
    "normalize_link",
    "normalize_title",
]
