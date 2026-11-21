"""统计年鉴：把"系列 → 版次 → 数据表"转成统一目录的资源。

表格文件的格式因版次而异（实测：2005–2012 多为网页表格，2013 起多为图片，个别有 xls），
只如实记录目录页给出的地址与扩展名，不臆测别的格式存在。
"""

from __future__ import annotations

from datafinder.catalog.entities import Distribution, Entry, RegionLink, TopicLink, make_id
from datafinder.catalog.files import distribution_for
from datafinder.catalog.rules import Target

from ..classify import regions_for, topics_for
from .parse import TocEntry
from .registry import Series


def source_id(series: Series | str) -> str:
    """年鉴来源 `yearbook.<id>`，普查资料来源 `census.<id>`（字符串参数按年鉴处理）。"""
    if isinstance(series, str):
        return f"yearbook.{series}"
    return f"{series.type}.{series.id}"


def series_resource_id(series: Series) -> str:
    return make_id(source_id(series), "series", series.id)


def edition_resource_id(series: Series, year: int) -> str:
    return make_id(source_id(series), "edition", str(year))


def _base_region(series: Series) -> list[RegionLink]:
    return [RegionLink(series.region, "covers", None, "exact", "structure")]


def series_entry(
    series: Series, *, access_status: str = "unknown", note: str | None = None
) -> Entry:
    """年鉴系列。拒绝机器人/连不上的站点只给人工入口（manual），并如实标注原因。"""
    manual = access_status in ("blocked", "unavailable")
    dist = Distribution(
        "manual" if manual else "page",
        series.home,
        note=note or ("站点拒绝自动访问，请用浏览器打开" if manual else None),
    )
    return Entry(
        source_id(series),
        "series",
        series.id,
        series.zh,
        landing_url=series.home,
        access_status=access_status,
        verification_level="source_asserted",
        regions=_base_region(series),
        topics=[],
        distributions=[dist],
        extra={"publisher": series.publisher, "crawlable": series.crawl},
    )


def edition_entry(series: Series, year: int, toc_url: str) -> Entry:
    landing = series.landing_for(year)
    return Entry(
        source_id(series),
        "edition",
        str(year),
        f"{series.zh} {series.label_for(year)}",
        parent_id=series_resource_id(series),
        landing_url=landing,
        edition_year=year,
        verification_level="source_asserted",
        regions=_base_region(series),
        distributions=[Distribution("page", landing)],
        extra={"toc_url": toc_url},
    )


_USABILITY_RANK = {"tabular": 0, "html_table": 1, "document": 2, "image": 3}


def _distributions(t: TocEntry) -> list[Distribution]:
    """同一张表目录里给出的各种格式：能直接分析的排前面（xls 先于网页表格先于图片）。"""
    seen, out = set(), []
    for url, fmt in ((t.url, t.fmt), *t.others):
        if url not in seen:
            seen.add(url)
            out.append(distribution_for(url, fmt))
    return _sorted(out)


def _sorted(dists: list[Distribution]) -> list[Distribution]:
    """可下载的在前，其次按可分析程度（xls 先于网页表格先于图片）。"""
    return sorted(
        dists,
        key=lambda d: (d.access_method != "direct_file", _USABILITY_RANK.get(d.usability or "", 9)),
    )


def table_key(t: TocEntry) -> str:
    return t.no or t.title[:20]


def listed_edition_entry(
    series: Series, year: int, page_url: str, distributions: list[Distribution]
) -> Entry:
    """只有版次列表的年鉴：版次页 + 可下载附件/在线浏览入口（不拆到表）。"""
    dists = _sorted(distributions)
    if all(d.url != page_url for d in dists):  # 版次链接本身就是文件时，不再重复一个页面入口
        dists.append(Distribution("page", page_url))
    return Entry(
        source_id(series),
        "edition",
        str(year),
        f"{series.zh} {year}",
        parent_id=series_resource_id(series),
        landing_url=page_url,
        edition_year=year,
        verification_level="source_asserted",
        topics=[TopicLink(x, "manual") for x in series.topics],
        regions=_base_region(series),
        distributions=dists,
        extra={"listed": True},
    )


def table_entry(series: Series, year: int, t: TocEntry, key_suffix: str = "") -> Entry:
    target = Target(top=t.chapter, path=[t.chapter], title=t.title)
    topics = topics_for(target)
    topics += [
        TopicLink(x, "manual") for x in series.topics if x not in {y.topic_id for y in topics}
    ]
    return Entry(
        source_id(series),
        "census_product" if series.type == "census" else "table",
        f"{year}/{table_key(t)}{key_suffix}",
        t.title,
        parent_id=edition_resource_id(series, year),
        path=t.chapter,
        landing_url=t.url,
        edition_year=year,
        verification_level="source_asserted",
        topics=topics,
        regions=regions_for(target, _base_region(series)),
        distributions=_distributions(t),
        extra={"no": t.no, "chapter": t.chapter, "series": series.id},
    )


def table_entries(series: Series, year: int, toc: list[TocEntry]) -> list[Entry]:
    """一个版次的全部数据表（说明类条目不收）。同一版次里表号重复时加后缀，保证资源 id 唯一。"""
    seen: dict[str, int] = {}
    out = []
    for t in toc:
        if t.kind != "table":
            continue
        key = table_key(t)
        seen[key] = seen.get(key, 0) + 1
        out.append(table_entry(series, year, t, f"#{seen[key]}" if seen[key] > 1 else ""))
    return out
