"""解析年鉴目录页。

统计出版社的"zk"模板（国家统计局及多数省份年鉴共用）：左侧目录页 `left.htm`，
章节是 `<li id="foldheader">一、综合</li>`，其下的 `<ul>` 里每张表一个 `<a href=…>1-1 标题</a>`。
各版次的表格文件格式不同（实测：2005–2012 为 HTML 页，2013 起多为图片 JPG，个别版次有 xls），
所以这里只如实记录**目录里给出的地址与扩展名**，不臆测别的格式是否存在。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

_TOKEN = re.compile(
    r"""(?isx)
    <li[^>]*id=["']?foldheader["']?[^>]*>(?P<chapter>.*?)</li>
  | <a\s[^>]*?href=["']?(?P<href>[^"'>\s]+)["']?[^>]*>(?P<inner>.*?)</a>
    """
)
_TAGS = re.compile(r"<[^>]+>")
_NUMBERED = re.compile(r"^(?:表\s*)?(\d+(?:[-.．]\d+)+)\s*(.+)$")
_SPACES = re.compile(r"[\s\u3000\xa0]+")
_PAREN = re.compile(r"[（(][^（）()]*[)）]")
# 标题里的年份：'2010-2024年规模以上工业增加值'、'……(2024年底)'、'2019年' 等，跨版次对齐时不应算作区别
_YEARS = re.compile(r"\d{4}(?:\s*[-—~～至]\s*\d{4})?\s*年(?:底|末|度)?")
_FORMATS = {"xls": "xls", "xlsx": "xlsx", "htm": "html", "html": "html", "jpg": "jpg", "jpeg": "jpg",
            "png": "png", "gif": "gif", "pdf": "pdf", "doc": "doc", "docx": "docx", "csv": "csv"}


@dataclass(frozen=True)
class TocEntry:
    chapter: str
    no: str  # 表号，如 "1-1"；说明类条目为空
    title: str
    url: str
    fmt: str
    kind: str  # table | note


def clean_text(html: str) -> str:
    return _SPACES.sub(" ", _TAGS.sub("", html)).strip()


def fmt_of(url: str) -> str:
    path = url.split("?")[0].split("#")[0]
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return _FORMATS.get(ext, ext or "other")


def norm_title(title: str) -> str:
    """跨年份对齐同一张表：去掉括号里的年份/说明、空白与标点。"""
    t = _YEARS.sub("", _PAREN.sub("", title))
    return re.sub(r"[\s\u3000，,、:：]+", "", t)


def decode(content: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


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
        url = urljoin(base_url, href)
        num = _NUMBERED.match(text)
        if num:
            entries.append(TocEntry(chapter, num.group(1).replace("．", "."), num.group(2).strip(), url, fmt_of(url), "table"))
        else:
            entries.append(TocEntry(chapter, "", text, url, fmt_of(url), "note"))
    return entries
