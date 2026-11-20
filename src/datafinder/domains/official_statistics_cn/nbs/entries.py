"""国家统计局：把分类树/指标清单（接口或旧索引）转成统一目录的资源。

一个库 = database；分类树中间节点 = category（仅用于逐级浏览）；叶子 = dataset；指标 = indicator。
实时抓取（adapter.discover）与一次性迁移（legacy 迁移）共用这里的构造函数，保证两条路径产出完全一致。
"""

from __future__ import annotations

import re

from datafinder.catalog.entities import Distribution, Entry, RegionLink, make_id
from datafinder.catalog.rules import Target

from ..classify import regions_for, topics_for
from .client import BASE, DATABASES, PAGE, clean_name

SOURCE_ID = "nbs"
FREQUENCY = {"YY": "annual", "MM": "monthly", "SS": "quarterly"}

# 各数据库的地域结构（由库的定义决定，证据 = structure）
_COUNTRY = [RegionLink("CN", "covers", "country", "exact", "structure")]
_PROVINCES = [RegionLink("CN", "covers", "province", "descendants", "structure")]
_CITIES = [RegionLink("CN", "covers", "prefecture", "descendants", "structure")]
_HK_MO_TW = [
    RegionLink(r, "covers", None, "exact", "structure") for r in ("CN-HK", "CN-MO", "CN-TW")
]
_WORLD = [RegionLink("WORLD", "covers", "country", "descendants", "structure")]
DB_REGIONS: dict[str, list[RegionLink]] = {
    **dict.fromkeys(("1", "2", "3"), _COUNTRY),
    **dict.fromkeys(("4", "5", "6"), _PROVINCES),
    **dict.fromkeys(("7", "8"), _CITIES),
    **dict.fromkeys(("9", "10"), _HK_MO_TW),
    **dict.fromkeys(("12", "13", "14"), _WORLD),
}

# 标题里自带的数据期："(2012-2022)"、"（2023-至今）"。只有起止年都明确（或"至今"）才算；"(-2022)"不猜起点。
_PERIOD = re.compile(r"[（(]\s*(\d{4})\s*[-—~至]\s*(\d{4}|至今)\s*[)）]")


def period_from_title(title: str) -> tuple[int | None, int | None]:
    m = _PERIOD.search(title)
    if not m:
        return None, None
    end = None if m.group(2) == "至今" else int(m.group(2))
    return int(m.group(1)), end


def database_id(code: str) -> str:
    return make_id(SOURCE_ID, "database", code)


def category_key(code: str, names: list[str]) -> str:
    return f"{code}/{' › '.join(names)}"


def _parent(code: str, names: list[str]) -> str:
    return make_id(SOURCE_ID, "category", category_key(code, names)) if names else database_id(code)


def database_entry(code: str) -> Entry:
    name, dt = DATABASES[code]
    return Entry(
        SOURCE_ID,
        "database",
        code,
        name,
        path=name,
        landing_url=PAGE,
        frequency=FREQUENCY[dt],
        verification_level="source_asserted",
        regions=list(DB_REGIONS[code]),
        distributions=[Distribution("page", PAGE, note="单页应用，需按分类逐级点开")],
    )


def category_entry(code: str, names: list[str]) -> Entry:
    """names：库名之后的各级分类名（不含库名）。"""
    db_name = DATABASES[code][0]
    return Entry(
        SOURCE_ID,
        "category",
        category_key(code, names),
        names[-1],
        parent_id=_parent(code, names[:-1]),
        path=" › ".join([db_name, *names]),
        verification_level="source_asserted",
    )


def dataset_entry(code: str, names: list[str], cid: str, root_id: str) -> Entry:
    """names：库名之后的各级名称，最后一个是数据集自己的名称。"""
    db_name, dt = DATABASES[code]
    title = names[-1]
    top = names[0]  # 直接挂在库下的数据集：顶层就是它自己的名称
    target = Target(top=top, path=names[:-1], title=title)
    start, end = period_from_title(title)
    return Entry(
        SOURCE_ID,
        "dataset",
        f"{code}/{cid}",
        title,
        parent_id=_parent(code, names[:-1]),
        path=" › ".join([db_name, *names]),
        landing_url=PAGE,
        period_start=start,
        period_end=end,
        frequency=FREQUENCY[dt],
        verification_level="source_asserted",
        topics=topics_for(target),
        regions=regions_for(target, DB_REGIONS[code]),
        distributions=[
            Distribution(
                "api", f"{BASE}/stream/esData", usability="tabular", note="POST JSON，需带 Referer"
            )
        ],
        extra={
            "db": code,
            "cid": cid,
            "root_id": root_id,
            **({"period_from": "title"} if start else {}),
        },
    )


def indicator_entry(ds: Entry, ind: dict) -> Entry:
    """ds：该指标所属的数据集条目；ind：{id, name, unit, mark}。"""
    code, cid = ds.extra["db"], ds.extra["cid"]
    title = clean_name(ind["name"], ind.get("unit")) or ind["name"]
    names = (ds.path or "").split(" › ")[1:]  # 去掉库名
    target = Target(top=names[0] if names else title, path=names[:-1], title=f"{ds.title} {title}")
    return Entry(
        SOURCE_ID,
        "indicator",
        f"{code}/{cid}/{ind['id']}",
        title,
        parent_id=ds.resource_id,
        path=ds.path,
        unit=ind.get("unit"),
        landing_url=PAGE,
        period_start=ds.period_start,
        period_end=ds.period_end,
        frequency=ds.frequency,
        verification_level="source_asserted",
        topics=topics_for(target),
        regions=regions_for(target, DB_REGIONS[code]),
        distributions=list(ds.distributions),
        extra={
            **ds.extra,
            "indicator_id": ind["id"],
            **({"mark": ind["mark"]} if ind.get("mark") else {}),
        },
    )
