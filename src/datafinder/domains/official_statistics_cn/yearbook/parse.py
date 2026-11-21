"""解析年鉴目录页。

统计出版社的"zk"模板（国家统计局及多数省份年鉴共用）：左侧目录页 `left.htm`，
章节是 `<li id="foldheader">一、综合</li>`，其下的 `<ul>` 里每张表一个 `<a href=…>1-1 标题</a>`。
各版次的表格文件格式不同（实测：2005–2012 为 HTML 页，2013 起多为图片 JPG，个别版次有 xls），
所以这里只如实记录**目录里给出的地址与扩展名**，不臆测别的格式是否存在。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit

from datafinder.catalog.files import fmt_of

_TOKEN = re.compile(
    r"""(?isx)
    <li[^>]*id=["']?foldheader["']?[^>]*>(?P<chapter>.*?)</li>
  | <a\s[^>]*?href=["']?(?P<href>[^"'>\s]+)["']?[^>]*>(?P<inner>.*?)</a>
    """
)
_TAGS = re.compile(r"<[^>]+>")
_NUMBERED = re.compile(
    r"^(?:表\s*)?((?:附录)?\s*\d+(?:[-.．]\d+)+)\s*(.+)$"
)  # 含"附录1-15 货币汇率"这类附录表
_SPACES = re.compile(r"[\s\u3000\xa0]+")


@dataclass(frozen=True)
class TocEntry:
    chapter: str
    no: str  # 表号，如 "1-1"；说明类条目为空
    title: str
    url: str
    fmt: str
    kind: str  # table | note
    others: tuple[tuple[str, str], ...] = ()  # 同一张表的其他格式 (url, fmt)，由合并多份目录得到


def clean_text(html: str) -> str:
    return _SPACES.sub(" ", _TAGS.sub("", html)).strip()


def parse_toc(html: str, base_url: str) -> list[TocEntry]:
    """目录页 → 条目列表。第一个章节之前的链接（版权声明、编委会等）不算数据表，直接忽略。"""
    entries: list[TocEntry] = []
    chapter: str | None = None
    for m in _TOKEN.finditer(html):
        if m.group("chapter") is not None:
            chapter = clean_text(m.group("chapter"))
            continue
        inner = m.group("inner")
        if "foldheader" in inner.lower():  # <a href=封面图><li id=foldheader>章名</li></a>
            chapter = clean_text(inner)
            continue
        if chapter is None:
            continue
        href = m.group("href").strip()
        if href.startswith(("javascript:", "#", "mailto:")):
            continue
        text = clean_text(inner)
        if not text:
            continue
        url = urljoin(
            base_url, href.strip().replace("\\", "/")
        )  # 老目录里有 html\\A0118C.HTM 这样的反斜杠
        num = _NUMBERED.match(text)
        if num:
            entries.append(
                TocEntry(
                    chapter,
                    _SPACES.sub("", num.group(1)).replace("．", "."),
                    num.group(2).strip(),
                    url,
                    fmt_of(url),
                    "table",
                )
            )
        else:
            entries.append(TocEntry(chapter, "", text, url, fmt_of(url), "note"))
    return entries


_TREE = re.compile(
    r"""(?isx)
    <div[^>]*class=["']?treeview-folder["']?[^>]*>(?P<folder>.*?)</div>
  | <a\s[^>]*?href=["']?(?P<href>[^"'>\s]+)["']?[^>]*>(?P<inner>.*?)</a>
    """
)
_CHAPTER = re.compile(r"^第[一二三四五六七八九十百零\d]+[篇章部分]")


def parse_tree_toc(html: str, base_url: str) -> list[TocEntry]:
    """树形目录页（上海统计年鉴）：`treeview-folder` 是篇名，其下 `<a href="nj25.htm?d1=2025tjnj/C0201.htm">表2.1 …</a>`。

    链接是查看器页面，真正的表格文件地址在 `d1` 参数里（相对目录页）。篇名只在"第X篇/章"处更新
    （子目录如"指标解释"不改变篇名）。
    """
    entries: list[TocEntry] = []
    chapter: str | None = None
    for m in _TREE.finditer(html):
        if m.group("folder") is not None:
            name = clean_text(m.group("folder"))
            if _CHAPTER.match(name):
                chapter = name
            continue
        if chapter is None:
            continue
        text = clean_text(m.group("inner"))
        if not text:
            continue
        href = m.group("href")
        d1 = parse_qs(urlsplit(href).query).get("d1")
        url = urljoin(base_url, d1[0] if d1 else href)
        num = _NUMBERED.match(text)
        if num:
            entries.append(
                TocEntry(
                    chapter,
                    _SPACES.sub("", num.group(1)).replace("．", "."),
                    num.group(2).strip(),
                    url,
                    fmt_of(url),
                    "table",
                )
            )
        else:
            entries.append(TocEntry(chapter, "", text, url, fmt_of(url), "note"))
    return entries
