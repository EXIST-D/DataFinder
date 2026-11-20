"""统计年鉴：把"系列 → 版次 → 数据表"转成统一目录的资源。

表格文件的格式因版次而异（实测：2005–2012 多为网页表格，2013 起多为图片，个别有 xls），
只如实记录目录页给出的地址与扩展名，不臆测别的格式存在。
"""

from __future__ import annotations

from datafinder.catalog.entities import Distribution, Entry, RegionLink, make_id
from datafinder.catalog.rules import Target

from ..classify import regions_for, topics_for
from .parse import TocEntry
from .registry import Series

# 文件扩展名 → (file_format, usability)。图片可以"下载"，但内容不能直接分析（usability=image）。
FORMATS = {
    "xls": ("xls", "tabular"), "xlsx": ("xlsx", "tabular"), "csv": ("csv", "tabular"),
    "html": ("html", "html_table"),
    "jpg": ("jpg", "image"), "png": ("png", "image"), "gif": ("gif", "image"),
    "pdf": ("pdf", "document"), "doc": ("doc", "document"), "docx": ("docx", "document"),
}


def source_id(series: Series | str) -> str:
    return f"yearbook.{series if isinstance(series, str) else series.id}"


def series_resource_id(series: Series) -> str:
    return make_id(source_id(series), "series", series.id)


def edition_resource_id(series: Series, year: int) -> str:
    return make_id(source_id(series), "edition", str(year))


def _base_region(series: Series) -> list[RegionLink]:
    return [RegionLink(series.region, "covers", None, "exact", "structure")]


def distribution_for(url: str, fmt: str) -> Distribution:
    if fmt in FORMATS:
        file_format, usability = FORMATS[fmt]
        return Distribution("direct_file", url, file_format, usability)
    return Distribution("page", url, note="目录页给出的链接格式不是常见文件，请在浏览器打开")


def series_entry(series: Series, *, access_status: str = "unknown", note: str | None = None) -> Entry:
    """年鉴系列。拒绝机器人/连不上的站点只给人工入口（manual），并如实标注原因。"""
    manual = access_status in ("blocked", "unavailable")
    dist = Distribution("manual" if manual else "page", series.home, note=note or ("站点拒绝自动访问，请用浏览器打开" if manual else None))
    return Entry(
        source_id(series), "series", series.id, series.zh, landing_url=series.home, access_status=access_status,
        verification_level="source_asserted", regions=_base_region(series),
        topics=[], distributions=[dist], extra={"publisher": series.publisher, "crawlable": series.crawl},
    )


def edition_entry(series: Series, year: int, toc_url: str) -> Entry:
    landing = series.base.format(year=year) + "indexch.htm" if series.base else series.home
    return Entry(
        source_id(series), "edition", str(year), f"{series.zh} {year}", parent_id=series_resource_id(series),
        landing_url=landing, edition_year=year, verification_level="source_asserted", regions=_base_region(series),
        distributions=[Distribution("page", landing)], extra={"toc_url": toc_url},
    )


def table_entry(series: Series, year: int, t: TocEntry, key_suffix: str = "") -> Entry:
    target = Target(top=t.chapter, path=[t.chapter], title=t.title)
    return Entry(
        source_id(series), "table", f"{year}/{t.no or t.title[:20]}{key_suffix}", t.title,
        parent_id=edition_resource_id(series, year), path=t.chapter, landing_url=t.url, edition_year=year,
        verification_level="source_asserted", topics=topics_for(target),
        regions=regions_for(target, _base_region(series)), distributions=[distribution_for(t.url, t.fmt)],
        extra={"no": t.no, "chapter": t.chapter, "series": series.id},
    )


def table_entries(series: Series, year: int, toc: list[TocEntry]) -> list[Entry]:
    """一个版次的全部数据表（说明类条目不收）。同一版次里表号重复时加后缀，保证资源 id 唯一。"""
    seen: dict[str, int] = {}
    out = []
    for t in toc:
        if t.kind != "table":
            continue
        key = t.no or t.title[:20]
        seen[key] = seen.get(key, 0) + 1
        out.append(table_entry(series, year, t, f"#{seen[key]}" if seen[key] > 1 else ""))
    return out
