"""栏目列表页的解析：把"一个栏目页"变成条目链接（标题、地址、日期）与翻页地址。

政府网站的列表页大多是 `<li><a href>标题</a><span>日期</span></li>`，翻页常见 TRS 模板
（`createPageHTML(总页数, 当前页, "index", "html")`，第 n 页是 `index_{n}.html`）。
这里只做通用启发式，不针对任何站点；站点差异由栏目配置（链接/标题正则、页数上限）承担。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

_DATE = re.compile(r"((?:19|20)\d{2})[-./年](\d{1,2})[-./月](\d{1,2})")
# 像"一篇文章/一个文件"的地址：含 6 位年月目录、t20260228_123 式文件名、content_/art_ 前缀，或文档类扩展名
_ITEM_HREF = re.compile(
    r"(/\d{6}/|/\d{4}/\d{1,2}/|t\d{8}_\d+|content_\d+|art_\d+|/art/\d{4}/|\.(?:pdf|xlsx?|docx?|csv|zip|rar)(?:$|\?))",
    re.I,
)
_CHROME_TEXT = {
    "首页",
    "上一页",
    "下一页",
    "尾页",
    "末页",
    "更多",
    "更多>>",
    "返回",
    "返回顶部",
    "无障碍",
    "网站地图",
    "联系我们",
    "English",
}
_PAGE_COUNT = re.compile(
    r"createPageHTML\(\s*(\d+)\s*,\s*\d+\s*,\s*[\"']([^\"']+)[\"']\s*,\s*[\"']([^\"']+)[\"']"
)
_SPACES = re.compile(r"[\s\u3000\xa0]+")
MIN_DATED = 3


@dataclass(frozen=True)
class Link:
    href: str  # 绝对地址（已去掉 #片段）
    text: str
    date: str | None = None  # YYYY-MM-DD（紧跟在链接后的日期）


class _Anchors(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.links: list[Link] = []
        self._href: str | None = None
        self._title: str | None = None
        self._text: list[str] = []
        self._after = 0  # 链接结束后还愿意往后看几段文字找日期

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        a = dict(attrs)
        self._href, self._title, self._text, self._after = a.get("href"), a.get("title"), [], 0

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)
        elif self._after > 0 and data.strip():
            self._after -= 1
            m = _DATE.search(data)
            if m and self.links and self.links[-1].date is None:
                y, mo, d = m.groups()
                last = self.links[-1]
                self.links[-1] = Link(last.href, last.text, f"{y}-{int(mo):02d}-{int(d):02d}")

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        href, text = self._href.strip(), _SPACES.sub("", "".join(self._text))
        self._href = None
        if not text and self._title:
            text = _SPACES.sub("", self._title)
        if not href or href.startswith(("javascript", "#", "mailto", "tel:")):
            return
        url = urldefrag(urljoin(self.base, href))[0]
        if urlsplit(url).scheme in ("http", "https"):
            self.links.append(Link(url, text))
            self._after = 3


# 动态栏目页（大汉/华宇等 CMS）把列表放在 <script> 里的 CDATA 中：先去掉这些包装，里面的 <a> 才解析得到
_UNWRAP = re.compile(r"</?script\b[^>]*>|<!\[CDATA\[|\]\]>", re.I)
_ARTICLE = re.compile(r"(t\d{8}_\d+|/art/\d{4}/\d{1,2}/\d{1,2}/|content_\d+|art_\d+_\d+)", re.I)


def looks_like_article(url: str) -> bool:
    """地址像一篇具体文章（而不是栏目页）。"""
    return bool(_ARTICLE.search(url))


_REDIRECT = re.compile(
    r"""window\.location(?:\.href)?\s*=\s*["']([^"']+)["']|<meta[^>]+http-equiv=["']?refresh["']?[^>]+content=["'][^"']*url=([^"'>\s]+)""",
    re.I,
)


def redirect_target(html: str, base_url: str) -> str | None:
    """很短的跳转页（JS 或 meta refresh）指向的地址；不是跳转页返回 None。"""
    if len(html) > 3000 or not (m := _REDIRECT.search(html)):
        return None
    return urldefrag(urljoin(base_url, (m.group(1) or m.group(2)).strip()))[0]


def extract_links(html: str, base_url: str) -> list[Link]:
    """页面里的全部链接（按首次出现顺序；同一地址合并：取最长的文字，日期取有的那个）。

    列表项常把同一个链接写三遍（适配不同屏宽），日期只跟在最后一个后面，所以必须合并。
    """
    p = _Anchors(base_url)
    p.feed(_UNWRAP.sub("", html))
    merged: dict[str, Link] = {}
    for link in p.links:
        if not link.text:
            continue
        old = merged.get(link.href)
        if old is None:
            merged[link.href] = link
        else:
            text = link.text if len(link.text) > len(old.text) else old.text
            merged[link.href] = Link(link.href, text, old.date or link.date)
    return list(merged.values())


def site_of(url: str) -> str:
    """站点键：同一站点的子域名视为同站（政府站点为 *.xx.gov.cn，取最后三段）。"""
    host = (urlsplit(url).hostname or "").lower()
    labels = host.split(".")
    return ".".join(labels[-3:] if host.endswith(".gov.cn") else labels[-2:])


def item_links(
    links: list[Link],
    page_url: str,
    *,
    href: str | None = None,
    text: str | None = None,
) -> list[Link]:
    """从页面链接里挑出"条目"：与本页同站、标题像标题、地址像文章/文件（或后面带日期）。

    href/text 是栏目配置里的正则；给了就以它为准（不再用默认启发式）。
    """
    href_re = re.compile(href) if href else None
    text_re = re.compile(text) if text else None
    home = site_of(page_url)
    out, seen = [], set()
    for link in links:
        if link.href == page_url or link.href in seen or site_of(link.href) != home:
            continue
        if len(link.text) < 4 or link.text in _CHROME_TEXT:
            continue
        if href_re:
            ok = bool(href_re.search(link.href))
        else:
            ok = bool(_ITEM_HREF.search(link.href)) or link.date is not None
        if not ok:
            continue
        seen.add(link.href)
        out.append(link)
    chosen = _list_region(out, bool(href_re))
    # 标题过滤放在"识别列表"之后：先确定哪些是列表条目，再按标题挑；否则只剩一两条时会被误判成"不是列表"
    return [x for x in chosen if not text_re or text_re.search(x.text)]


def _list_region(out: list[Link], explicit: bool) -> list[Link]:
    if explicit:
        return out
    # 没有自定义正则时：列表页的条目都带发布日期，页脚/侧栏的链接没有——能区分时只留带日期的
    dated = [x for x in out if x.date]
    if len(dated) >= MIN_DATED:
        return dated
    # 没有日期可依：条目应当集中在同一个一级目录下；分散在各处的是导航/推荐位，不当作列表
    groups: dict[str, list[Link]] = {}
    for x in out:
        groups.setdefault(urlsplit(x.href).path.strip("/").split("/")[0], []).append(x)
    best = max(groups.values(), key=len, default=[])
    return best if len(best) >= MIN_DATED else []


def total_pages(html: str) -> int:
    """TRS 模板声明的总页数；没有翻页声明按 1 页。"""
    m = _PAGE_COUNT.search(html)
    return int(m.group(1)) if m else 1


def page_url(first_url: str, n: int, html: str | None = None) -> str:
    """第 n 页（n≥2）的地址：TRS 模板为同目录的 index_{n-1}.html（页码从 0 起）。"""
    name, ext = "index", "html"
    if html and (m := _PAGE_COUNT.search(html)):
        name, ext = m.group(2), m.group(3)
    base = first_url if first_url.endswith("/") else first_url.rsplit("/", 1)[0] + "/"
    return f"{base}{name}_{n - 1}.{ext}"


_YEAR = re.compile(r"((?:19|20)\d{2})\s*年?")


def infer_period(text: str, date: str | None) -> tuple[int | None, str | None, int | None]:
    """从条目标题与发布日期推断 (观测年, 频度, 出版年)。只做保守推断，认不出就留空。

    "2025年国民经济…统计公报" → 年度；"2026年10月份国民经济运行情况" → 月度；"2026年三季度…" → 季度。
    """
    published = int(date[:4]) if date else None
    m = _YEAR.search(text)
    if not m:
        return None, None, published
    year = int(m.group(1))
    rest = text[m.end() :]
    if re.match(
        r"\s*(?:\d{1,2}月|[1-9]-\d{1,2}月|[一二三四]季度|[1-4]季度|上半年|1-\d)", rest
    ) or re.search(r"\d{1,2}月份", text):
        freq = "monthly"
        if re.search(r"季度|上半年", text):
            freq = "quarterly"
    elif re.search(r"季度|上半年", text):
        freq = "quarterly"
    elif text.strip() == m.group(0).strip() or re.search(r"年度|全年|统计公报|年鉴", text):
        freq = "annual"
    else:
        freq = None
    return year, freq, published
