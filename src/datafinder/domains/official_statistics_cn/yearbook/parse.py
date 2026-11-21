"""解析年鉴目录页。

统计出版社的"zk"模板（国家统计局及多数省份年鉴共用）：左侧目录页 `left.htm`，
章节是 `<li id="foldheader">一、综合</li>`，其下的 `<ul>` 里每张表一个 `<a href=…>1-1 标题</a>`。
各版次的表格文件格式不同（实测：2005–2012 为 HTML 页，2013 起多为图片 JPG，个别版次有 xls），
所以这里只如实记录**目录里给出的地址与扩展名**，不臆测别的格式是否存在。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

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


def _make(chapter: str, text: str, url: str) -> TocEntry:
    """一条目录项：文字以"表号 标题"开头就是数据表，否则当说明类条目。"""
    num = _NUMBERED.match(text)
    if num:
        return TocEntry(
            chapter,
            _SPACES.sub("", num.group(1)).replace("．", "."),
            num.group(2).strip(),
            url,
            fmt_of(url),
            "table",
        )
    return TocEntry(chapter, "", text, url, fmt_of(url), "note")


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


# ── 其他目录形态 ──
# 各省年鉴并不都用统计出版社的 zk 模板，下面几个解析器对应实测到的另外四种目录页：
#   json  安徽：整个目录内嵌在页面脚本里（const contents='[{label,link,children}]'）
#   ml    吉林：Word 导出的左侧目录（<span class="Outline">第1章 综合</span> + <a href="../html/1-1.html">）
#   yb    浙江：查看器目录树（<div class="yb-submenu-title">一、综合</div> + <a main="./cn/html/1-1 …html">）
#   fp    江苏：FrontPage 两级目录（indexc.htm → 章节页 nj01.htm → 每表一个 njXX/njXXYY.htm）

_JSON_CONTENTS = re.compile(r"const\s+contents\s*=", re.S)


def _json_array_at(text: str, start: int) -> str | None:
    """从 start 处的 `[` 开始按括号配平截出 JSON 数组（字符串里的括号不参与）。"""
    depth, i, n = 0, start, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None


def parse_json_toc(html: str, base_url: str) -> list[TocEntry]:
    """安徽：目录是内嵌 JSON。顶层节点是篇（有 children），叶子是 {label, link}。

    页面里的字符串用单引号包 JSON，且结尾引号不稳定（实测有页面缺少闭合引号），
    因此不靠引号定位，改为从 `[` 起按括号配平截取。
    """
    import json

    m = _JSON_CONTENTS.search(html)
    if not m:
        return []
    start = html.find("[", m.end())
    if start < 0:
        return []
    blob = _json_array_at(html, start)
    if not blob:
        return []
    try:
        nodes = json.loads(blob)
    except ValueError:
        return []
    out: list[TocEntry] = []
    for node in nodes if isinstance(nodes, list) else []:
        chapter = str(node.get("label") or "")
        kids = node.get("children")
        if not kids:
            link = node.get("link")
            if link:
                out.append(_make("", chapter, urljoin(base_url, link)))
            continue
        for kid in kids:
            link = kid.get("link")
            if link and kid.get("label"):
                out.append(_make(chapter, str(kid["label"]).strip(), urljoin(base_url, link)))
    return out


_ML_TABLE = re.compile(r"\d+[-_]\d+\.html?$", re.I)
_ML_CHAPTER_NAME = re.compile(
    r"第[一二三四五六七八九十百零\d]{1,6}[章篇][、.．]?\s*[\u4e00-\u9fff]{1,12}"
)
# 章节标题两种写法：2025 版在 <a> 里的 <span class="Outline">，2022/2023 版是裸的 <u class="Outline">
_ML_TOKEN = re.compile(
    r"""(?isx)
    <(?:u|span)\b[^>]*class=["']?Outline["']?[^>]*>(?P<chapter>.*?)</(?:u|span)>
  | <a\s[^>]*?href=["']?(?P<href>[^"'>\s]+)["']?[^>]*>(?P<inner>.*?)</a>
    """
)


def parse_ml_toc(html: str, base_url: str) -> list[TocEntry]:
    """吉林：章节文字是 `第1章 综合`（2025 版）或 `第一篇、综合`（更早的版次），
    表是 `<a href="../html/1-1.html">` / `<a href="../2024/B1_1.htm">`。"""
    entries: list[TocEntry] = []
    chapter: str | None = None
    for m in _ML_TOKEN.finditer(html):
        if m.group("chapter") is not None:
            found = _ML_CHAPTER_NAME.search(clean_text(m.group("chapter")))
            if found:
                chapter = found.group(0).strip()
            continue
        href = m.group("href").strip()
        text = clean_text(m.group("inner"))
        found = _ML_CHAPTER_NAME.search(text)
        if found:
            chapter = found.group(0).strip()
            if not _ML_TABLE.search(href):
                continue
        if chapter is None or not _ML_TABLE.search(href):
            continue
        if text:
            entries.append(_make(chapter, text, urljoin(base_url, href)))
    return entries


_YB_TOKEN = re.compile(
    r"""(?isx)
    <div[^>]*class=["']?yb-submenu-title["']?[^>]*>(?P<chapter>.*?)</div>
  | <a\s[^>]*?\bmain=["'](?P<main>[^"']+)["'][^>]*>(?P<inner>.*?)</a>
    """
)


def parse_yb_toc(html: str, base_url: str) -> list[TocEntry]:
    """浙江：查看器目录树。章节在 `yb-submenu-title`，表地址在 `<a main="...">`。"""
    entries: list[TocEntry] = []
    chapter = ""
    for m in _YB_TOKEN.finditer(html):
        if m.group("chapter") is not None:
            chapter = clean_text(m.group("chapter"))
            continue
        text = clean_text(m.group("inner"))
        main = m.group("main").strip()
        if not text or not main:
            continue
        entries.append(_make(chapter, text, urljoin(base_url, main)))
    return entries


_FP_CHAPTER = re.compile(r"nj\d+\.html?$", re.I)
_FP_TABLE = re.compile(r"(?:^|/)nj\d+/nj\d+\.html?$", re.I)


def parse_fp_chapters(html: str, base_url: str) -> list[str]:
    """江苏：索引页 indexc.htm 里的章节页（nj01.htm…），按出现顺序去重。"""
    out: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
        if _FP_CHAPTER.search(href):
            url = urljoin(base_url, href)
            if url not in out:
                out.append(url)
    return out


def parse_fp_toc(html: str, base_url: str) -> list[TocEntry]:
    """江苏：章节页（nj01.htm）里每张表一个页面（nj01/nj0100.htm），标题形如"1-1 行政区划"。"""
    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    chapter = clean_text(title.group(1)) if title else ""
    entries: list[TocEntry] = []
    for m in re.finditer(
        r"""<a\s[^>]*?href=["'](?P<href>[^"']+)["'][^>]*>(?P<inner>.*?)</a>""", html, re.S | re.I
    ):
        href = unquote(m.group("href")).strip()
        if not _FP_TABLE.search(href):
            continue
        text = clean_text(m.group("inner"))
        if text:
            entries.append(_make(chapter, text, urljoin(base_url, m.group("href").strip())))
    return entries


_FJ_TOKEN = re.compile(
    r"""(?isx)
    <div[^>]*class=["']?chapterItem["']?[^>]*>(?P<chapter>.*?)</div>
  | <a\s[^>]*?href=["'](?P<href>[^"']+)["'][^>]*>(?P<inner>.*?)</a>
    """
)
_FJ_TABLE = re.compile(r"\d{3,4}\.html?$", re.I)
_FJ_SPACE = re.compile(r"&nbsp;|&#160;", re.I)


def parse_fj_toc(html: str, base_url: str) -> list[TocEntry]:
    """福建：2011–2022 版是光盘版树形目录（章节 `<div class="chapterItem">一 综合</div>`，
    表在 `ul.reports` 里，如 `<a href="./cn/html/0101.htm">1-1 全省行政区划</a>`）；
    2023 起换成统计出版社的 zk 目录页，直接交给 `parse_toc`。"""
    if "chapterItem" not in html:
        return parse_toc(html, base_url)
    entries: list[TocEntry] = []
    chapter: str | None = None
    for m in _FJ_TOKEN.finditer(html):
        if m.group("chapter") is not None:
            chapter = _FJ_SPACE.sub(" ", clean_text(m.group("chapter"))).strip()
            continue
        if chapter is None:
            continue
        href = m.group("href").strip()
        if not _FJ_TABLE.search(href):
            continue
        text = _FJ_SPACE.sub(" ", clean_text(m.group("inner"))).strip()
        if text:
            entries.append(_make(chapter, text, urljoin(base_url, href)))
    return entries


_HEB_FOLD = re.compile(r"<li[^>]*id=[\"']?foldheader[\"']?[^>]*>(.*?)</li>", re.S | re.I)
_HEB_ANCHOR = re.compile(
    r"<a\s[^>]*?href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<inner>.*?)</a>", re.S | re.I
)
# 2008–2012：/extra/col20/[年/]0101.htm（前两位=篇，后两位=表）
_HEB_COL20 = re.compile(r"/extra/col20/(?:\d{4}/)?(?P<stem>\d{4,8})\.html?$", re.I)
# 2013–2019：图片版，html/0001.jpg 按整本顺序编号，推不出"篇-表"
_HEB_NUMFILE = re.compile(r"/(?P<stem>\d{4,8})\.(?:jpe?g|png|html?|xlsx?)$", re.I)
_PDF_LINK = re.compile(r"\.pdf$", re.I)


def parse_heb_toc(html: str, base_url: str) -> list[TocEntry]:
    """河北：三代形态共用一个入口。

    - 2008–2012：文章页里嵌着 foldheader 折叠目录，表是 `/extra/col20/[年份/]0101.htm`，
      标题不带表号——表号从文件名还原（0101→1-1，010101→1-1-1）；
    - 2013–2019：同样是折叠目录，但表是**图片**（html/0001.jpg，按整本顺序编号）或整篇 PDF；
    - 2020 起：统计出版社的 zk 目录页（标题自带表号），交给 `parse_toc`。

    章节标题有时被包在 `<a href="#">` 里（2019 版），按"位置"归属比按嵌套解析稳。
    """
    zk = parse_toc(html, base_url)  # 2020 起是标准 zk 目录页（标题自带表号），优先用它
    if any(e.kind == "table" and e.no for e in zk):
        return zk
    chapters = [
        (m.start(), clean_text(m.group(1)))
        for m in _HEB_FOLD.finditer(html)
        if clean_text(m.group(1))
    ]
    if not chapters:
        return zk
    entries: list[TocEntry] = []
    docs: list[tuple[int, str, str, str]] = []
    for m in _HEB_ANCHOR.finditer(html):
        pos = m.start()
        name = next((n for p, n in reversed(chapters) if p < pos), None)
        if name is None:
            continue
        href = m.group("href").strip()
        text = clean_text(m.group("inner"))
        if not text:
            continue
        col20 = _HEB_COL20.search(href)
        if col20:
            stem = col20.group("stem")
            no = "-".join(str(int(stem[i : i + 2])) for i in range(0, len(stem), 2))
            entries.append(TocEntry(name, no, text, urljoin(base_url, href), fmt_of(href), "table"))
            continue
        numf = _HEB_NUMFILE.search(href)
        if numf:
            entries.append(TocEntry(name, "", text, urljoin(base_url, href), fmt_of(href), "table"))
            continue
        if _PDF_LINK.search(href):
            docs.append((pos, name, text, urljoin(base_url, href)))
    if entries:
        return entries
    if docs:
        # 整篇 PDF：没有表号，标题保持原样，只是这一条是一份文档而不是一张表
        return [
            TocEntry(name, "", f"{text}（整篇）", url, "pdf", "table")
            for _, name, text, url in docs
        ]
    return parse_toc(html, base_url)


_BOOK_ANCHOR = re.compile(
    r"""<a\s[^>]*?href=["'](?P<href>[^"']+)["'][^>]*>(?P<inner>.*?)</a>""", re.S | re.I
)
_BOOK_TABLE = re.compile(r"/(?P<ch>\d+)-(?P<no>\d+)\.html?$", re.I)
_BOOK_CHAPTER = ("#", "javascript:;", "javascript:void(0)", "javascript:void(0);")


def parse_book_toc(html: str, base_url: str) -> list[TocEntry]:
    """旧版光盘目录（山西 2012–2014 这类）：没有折叠标记，

    - 章节是 `<a href="#">综 合</a>`（占位链接，不跳转）；
    - 表是 `<a href="../book/1-1.htm">行政区划(2013年)</a>`，表号就在文件名里。

    已经是标准 zk 目录页时（2015 起）直接交给 `parse_toc`。
    """
    zk = parse_toc(html, base_url)
    if any(e.kind == "table" for e in zk):
        return zk
    entries: list[TocEntry] = []
    chapter: str | None = None
    for m in _BOOK_ANCHOR.finditer(html):
        href = m.group("href").strip()
        text = clean_text(m.group("inner"))
        if not text:
            continue
        if href.lower() in _BOOK_CHAPTER:
            chapter = text
            continue
        hit = _BOOK_TABLE.search(href)
        if hit and chapter:
            no = f"{int(hit.group('ch'))}-{int(hit.group('no'))}"
            entries.append(
                TocEntry(chapter, no, text, urljoin(base_url, href), fmt_of(href), "table")
            )
    return entries or zk
