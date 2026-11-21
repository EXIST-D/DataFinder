"""省级统计站点的渠道探测：把"数据放在哪个栏目、目录是什么形态"变成一次可复用的扫描。

这一轮手工摸过 20 多个省，判断规则其实是稳定的，只是散在人脑里：

1. 首页/数据栏目 → 找「统计年鉴、统计公报、普查、数据查询、指标」这些栏目链接；
2. 年鉴栏目 → 抽「XX年鉴—YYYY」的版次链接（年份在标题里，也在 `nj2024` 这类路径里）；
3. 版次地址 → 依次试 `left.htm`、`lefte.htm`、`contents-cn.htm`…… 拿到目录页；
4. 目录页 → 按标记判断形态（zk / fj / yb / ml / json / fp / heb / book），
   再交给对应的解析器数一下能出多少张表。

`detect_format` 里的标记都是实测来的，注释写明了对应省份，新增形态时在这里加一条即可。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from datafinder.catalog.files import decode_html
from datafinder.engine.http import Http

from .yearbook.parse import PARSERS  # 与适配器共用同一份解析器表

YEAR = re.compile(r"(19|20)\d{2}")
TOC_CANDIDATES = ("left.htm", "lefte.htm", "contents-cn.htm", "indexch.htm", "left_new.htm")

# 版次年份：标题里「年鉴」后面紧跟的年份优先，其次路径里的 njYYYY
_TITLE_YEAR = re.compile(r"年鉴[^\d]{0,6}((?:19|20)\d{2})")
_PATH_YEAR = re.compile(r"(?<![\d])((?:19|20)\d{2})(?![\d])")


# 文章/新闻页的路径特征（各省 CMS 大同小异）：/202611/t2026...、/art/2024/、150736.html
_ARTICLE = re.compile(r"/\d{4,6}/t\d{6,}|/art/\d{4}/|/\d{7,}\.html?$")

# 栏目关键词 → 类型。顺序即优先级：先认"年鉴"，否则"统计年鉴"会被"统计数据"抢走
CHANNEL_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("年鉴", re.compile(r"年鉴")),
    ("公报", re.compile(r"公报")),
    ("普查", re.compile(r"普查")),
    ("指标", re.compile(r"指标查询|指标库|指标数据")),
    ("数据", re.compile(r"数据查询|数据检索|统计数据|数据平台|数据库|数据开放|数据发布|数据解读")),
    ("制度", re.compile(r"统计制度|统计标准|指标解释|统计分类")),
)


@dataclass
class Channel:
    kind: str
    title: str
    url: str


@dataclass
class Edition:
    year: int
    title: str
    url: str


@dataclass
class TocProbe:
    edition: int
    url: str
    toc: str
    format: str
    tables: int


@dataclass
class SiteProbe:
    url: str
    channels: list[Channel] = field(default_factory=list)
    editions: list[Edition] = field(default_factory=list)
    tocs: list[TocProbe] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def best(self) -> TocProbe | None:
        """能出表的探测结果里，表最多的一条——通常就是主系列该用的形态。"""
        good = [t for t in self.tocs if t.tables > 0]
        return max(good, key=lambda t: t.tables) if good else None


def classify(text: str) -> str | None:
    for kind, pattern in CHANNEL_RULES:
        if pattern.search(text):
            return kind
    return None


def find_channels(html: str, base_url: str, *, limit: int = 40) -> list[Channel]:
    """页面里像"数据栏目"的链接（同站、标题够短），按类型去重。"""
    host = urlsplit(base_url).netloc
    out: list[Channel] = []
    seen: set[str] = set()
    for m in re.finditer(
        r"""<a\s[^>]*?href=["'](?P<href>[^"']+)["'][^>]*>(?P<inner>.*?)</a>""", html, re.S | re.I
    ):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group("inner"))).strip()
        if not text or len(text) > 16:
            continue
        href = urljoin(base_url, m.group("href").strip())
        if urlsplit(href).netloc != host or href in seen:
            continue
        if _ARTICLE.search(urlsplit(href).path):  # 新闻/文章页不是栏目
            continue
        kind = classify(text)
        if not kind:
            continue
        seen.add(href)
        out.append(Channel(kind, text, href))
        if len(out) >= limit:
            break
    return out


def find_editions(html: str, base_url: str, *, name: str = "年鉴") -> list[Edition]:
    """从年鉴栏目页里抽「名称 + 年份」的版次链接；年份优先取标题，其次取路径里的 njYYYY。"""
    out: dict[int, Edition] = {}
    for m in re.finditer(
        r"""<a\s[^>]*?href=["'](?P<href>[^"']+)["'][^>]*>(?P<inner>.*?)</a>""", html, re.S | re.I
    ):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group("inner"))).strip()
        if name not in text:
            continue
        href = m.group("href").strip()
        # 标题里「年鉴」后面紧跟的年份才算版次（"关于年鉴的说明"这类文章不算）
        hit = _TITLE_YEAR.search(text)
        if not hit and not _ARTICLE.search(urlsplit(href).path):
            hit = _PATH_YEAR.search(href)
        if not hit:
            continue
        year = int(hit.group(1))
        url = urljoin(base_url, href)
        out.setdefault(year, Edition(year, text, url))
    return [out[y] for y in sorted(out, reverse=True)]


def detect_format(html: str) -> str | None:
    """按页面标记判断目录形态。标记全是实测来的，注释里写明出处。"""
    if "chapterItem" in html:  # 福建 2011–2022 光盘版树形目录
        return "fj"
    if "mergeCells" in html or "templetType" in html:  # 浙江查看器的脚本网格
        return "yb"
    if "/extra/col20/" in html:  # 河北 2008–2012 文章页内嵌目录
        return "heb"
    if re.search(r"/book/\d+-\d+\.[Hh]tm", html):  # 山西 2012–2014 旧光盘版
        return "book"
    if re.search(r"(?:const|var)\s+contents\s*=", html):  # 安徽页面内嵌 JSON
        return "json"
    if re.search(r"""class=["']?Outline""", html):  # 吉林 Word 导出的左侧目录
        return "ml"
    if "foldheader" in html:  # 统计出版社 zk；河北 2013–2019 的图片表（html/0001.jpg）也走 heb
        numeric_image = re.search(r"""href=["'][^"'\s]*/\d{4,8}\.(?:jpe?g|pdf)["']""", html, re.I)
        return "heb" if numeric_image else "zk"
    if re.search(r"indexc?\.htm|nj\d+\.htm", html):  # 江苏 FrontPage 两级目录
        return "fp"
    return None


def toc_urls_for(edition_url: str) -> list[str]:
    """版次地址 → 该试的目录地址。`.../indexch.htm` 取同目录，`.../zk/` 直接拼文件名。"""
    base = edition_url if edition_url.endswith("/") else edition_url.rsplit("/", 1)[0] + "/"
    urls = [base + name for name in TOC_CANDIDATES]
    if not edition_url.endswith("/") and edition_url not in urls:
        urls.insert(0, edition_url)
    return urls


def count_tables(html: str, url: str, fmt: str) -> int:
    parser = PARSERS.get(fmt)
    if not parser:
        return 0
    return sum(1 for e in parser(html, url) if e.kind == "table")


async def probe(url: str, http: Http, *, editions: int = 3) -> SiteProbe:
    """扫一个站点：栏目 → 年鉴版次 → 每个版次的目录形态与表数。请求数有上限。"""
    report = SiteProbe(url)
    code, body = await http.get_bytes(url, timeout=20)
    if code != 200:
        report.notes.append(f"首页返回 HTTP {code}")
        return report
    home = decode_html(body)
    report.channels = find_channels(home, url)
    if not report.channels:
        # 首页是脚本外壳时，试几个常见的"数据"聚合路径（安徽、河南这类站点常见）
        for guess in ("tjsj/", "sj/", "tjnj/", "sjcx/"):
            code, body = await http.get_bytes(urljoin(url, guess), timeout=15)
            if code == 200:
                found = find_channels(decode_html(body), urljoin(url, guess))
                if found:
                    report.channels = found
                    report.notes.append(f"首页导航是脚本，改用 {guess} 找到栏目")
                    break
    if not report.channels:
        report.notes.append("首页没找到数据栏目（导航可能是脚本渲染）")
        return report

    # 年鉴入口有时不在首页，而在"统计数据/普查"栏目里（山东就是这样）
    candidates = [c for c in report.channels if c.kind == "年鉴"] or [
        c for c in report.channels if c.kind in ("数据", "普查")
    ][:2]
    for page in candidates:
        code, body = await http.get_bytes(page.url, timeout=20)
        if code != 200:
            continue
        report.editions = find_editions(decode_html(body), page.url)
        if report.editions:
            break
    if not report.editions:
        report.notes.append("栏目里没抽到「名称+年份」的版次链接")
        return report

    for ed in report.editions[:editions]:
        for toc in toc_urls_for(ed.url):
            code, body = await http.get_bytes(toc, timeout=20)
            if code != 200:
                continue
            html = decode_html(body)
            fmt = detect_format(html)
            if not fmt:
                continue
            tables = count_tables(html, toc, fmt)
            report.tocs.append(TocProbe(ed.year, ed.url, toc, fmt, tables))
            if tables:
                break
    return report


async def probe_report(url: str, *, editions: int = 3) -> dict:
    """给核心扩展点用的包装：自己开连接、返回可直接序列化的报告。"""
    from dataclasses import asdict

    http = Http(default_interval=1.0)
    try:
        report = await probe(url, http, editions=editions)
    finally:
        await http.aclose()
    return {
        "url": report.url,
        "channels": [asdict(c) for c in report.channels],
        "editions": [asdict(e) for e in report.editions],
        "tocs": [asdict(t) for t in report.tocs],
        "best": asdict(report.best()) if report.best() else None,
        "notes": report.notes,
    }
