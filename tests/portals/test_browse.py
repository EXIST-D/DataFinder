import pytest

from datafinder.portals.browse import Filters, browse, facets, portal_dict
from datafinder.portals.facets import is_official, kind_of, openness_of, tier_of, tier_zh
from datafinder.portals.sync import Portal, write_db
from datafinder.store import connect


def P(id, name, **kw):
    kw.setdefault("source", "dateno")
    kw.setdefault("url", f"https://{id}.example.org")
    return Portal(id=id, name=name, **kw)


PORTALS = [
    P(
        "nbs",
        "NBS",
        name_zh="国家统计局",
        country="CN",
        catalog_type="Indicators catalog",
        owner_type="Central government",
        industries=["statistics", "economy"],
        featured=True,
        is_national=True,
        has_api=True,
        access="open",
    ),
    P(
        "gd",
        "Guangdong Stats",
        country="CN",
        region="CN-GD",
        catalog_type="Indicators catalog",
        owner_type="Regional government",
        industries=["statistics"],
        labels=["stats_bureau"],
        verify="ok",
        access="open",
    ),
    P(
        "sh",
        "Shanghai Open Data",
        country="CN",
        region="CN-SH",
        catalog_type="Open data portal",
        owner_type="Local government",
        industries=["government"],
        labels=["open_platform"],
        note="需注册",
        has_api=True,
        access="open, restricted",
    ),
    P(
        "cma",
        "CMA",
        country="CN",
        catalog_type="Scientific data repository",
        owner_type="Academy",
        industries=["climate"],
        access="open",
    ),
    P(
        "wb",
        "World Bank",
        country="World",
        catalog_type="Indicators catalog",
        owner_type="International",
        industries=["economy", "statistics"],
        has_api=True,
        access="open",
    ),
    P(
        "us",
        "US Gov",
        country="US",
        catalog_type="Open data portal",
        owner_type="Federal government",
        industries=["government"],
        access="open",
    ),
    P(
        "biz",
        "A Vendor",
        country="US",
        catalog_type="Data marketplace",
        owner_type="Business",
        industries=["economy"],
        access="restricted",
    ),
]


@pytest.fixture()
def conn():
    c = connect(":memory:")
    write_db(c, [*PORTALS], {"registry_ref": "test"})
    return c


def ids(rows):
    return [r["id"] for r in rows]


# ───────────── 分类映射 ─────────────


def test_tier_mapping_covers_all_owner_types_and_never_drops_unknown():
    assert tier_of("International") == 1
    assert tier_of("Central government") == tier_of("Federal government") == 2
    assert tier_of("Regional government") == tier_of("Local government") == 3
    assert tier_of("Academy") == 4 and tier_of("Business") == 5
    assert tier_of("Civil society") == tier_of("Community") == tier_of("Other") == 6
    assert tier_of("Something new") == 6  # 未知类型不能从筛选里消失
    assert tier_of(None) is None
    assert is_official(1) and is_official(3) and not is_official(4) and not is_official(None)
    assert tier_zh(2) == "国家级政府"


def test_kind_and_openness_mapping():
    assert kind_of("Indicators catalog") == "statistics"
    assert kind_of("General research repository") == "science"
    assert kind_of("Something else") == "other"
    assert openness_of("open") == "open"
    assert openness_of("open, restricted") == "mixed"
    assert openness_of("restricted") == "restricted"


# ───────────── 浏览与排序 ─────────────


def test_official_first_sort_puts_featured_national_then_official_tiers(conn):
    rows, total = browse(conn, Filters())
    assert total == len(PORTALS)
    order = ids(rows)
    assert order[0] == "nbs"  # 精选 + 国家级
    assert order.index("wb") < order.index("cma") < order.index("biz")  # 官方(国际) → 科研 → 商业


def test_filters_combine(conn):
    rows, total = browse(conn, Filters(country="CN", domain="statistics"))
    assert set(ids(rows)) == {"nbs", "gd"} and total == 2
    rows, _ = browse(conn, Filters(official=True))
    assert set(ids(rows)) == {"nbs", "gd", "sh", "wb", "us"}
    rows, _ = browse(conn, Filters(tiers=[4, 5]))
    assert set(ids(rows)) == {"cma", "biz"}
    rows, _ = browse(conn, Filters(api=True))
    assert set(ids(rows)) == {"nbs", "sh", "wb"}
    rows, _ = browse(conn, Filters(openness="mixed"))
    assert ids(rows) == ["sh"]
    rows, _ = browse(conn, Filters(label="stats_bureau"))
    assert ids(rows) == ["gd"]
    rows, _ = browse(conn, Filters(country="CN", region="CN-SH"))
    assert ids(rows) == ["sh"]


def test_pagination(conn):
    first, total = browse(conn, Filters(), size=3, page=1)
    second, _ = browse(conn, Filters(), size=3, page=2)
    assert len(first) == 3 and total == 7 and not set(ids(first)) & set(ids(second))
    last, _ = browse(conn, Filters(), size=3, page=3)
    assert len(last) == 1


# ───────────── 分面计数 ─────────────


def by_id(items):
    return {i["id"]: i["count"] for i in items}


def test_facet_counts_without_filters(conn):
    fx = facets(conn, Filters())
    assert fx["total"] == 7
    assert by_id(fx["domains"])["statistics"] == 3 and by_id(fx["domains"])["economy"] == 3
    assert by_id(fx["countries"]) == {"CN": 4, "World": 1, "US": 2}
    assert by_id(fx["tiers"]) == {1: 1, 2: 2, 3: 2, 4: 1, 5: 1, 6: 0}
    assert fx["official"] == 5 and fx["api"] == 3
    assert fx["regions"] == []  # 没选国家不给省份


def test_a_facet_does_not_count_its_own_filter(conn):
    # 选了"统计"领域：领域磁贴上其他领域的条数仍按"其他条件"算，不会全部变成 0
    fx = facets(conn, Filters(domain="statistics"))
    assert by_id(fx["domains"])["economy"] == 3
    assert fx["total"] == 3
    # 但国家的条数要反映"统计"这个已选条件
    assert by_id(fx["countries"]) == {"CN": 2, "World": 1}


def test_other_filters_narrow_each_facet(conn):
    fx = facets(conn, Filters(country="CN"))
    assert by_id(fx["tiers"])[3] == 2 and by_id(fx["tiers"])[2] == 1
    assert by_id(fx["regions"]) == {"CN-GD": 1, "CN-SH": 1}
    # 选了国家后，其他国家的条数仍然显示（便于切换）
    assert by_id(fx["countries"]) == {"CN": 4, "World": 1, "US": 2}


def test_selected_region_keeps_sibling_regions_visible(conn):
    fx = facets(conn, Filters(country="CN", region="CN-GD"))
    assert by_id(fx["regions"]) == {"CN-GD": 1, "CN-SH": 1}
    assert fx["total"] == 1


def test_tier_facet_ignores_its_own_selection(conn):
    fx = facets(conn, Filters(tiers=[4]))
    assert by_id(fx["tiers"])[1] == 1  # 选了档位 4，档位 1 的条数仍可见
    assert fx["total"] == 1


# ───────────── 展示字段 ─────────────


def test_portal_dict_has_display_fields(conn):
    rows, _ = browse(conn, Filters(label="open_platform"))
    d = portal_dict(rows[0])
    assert d["name"] == "Shanghai Open Data" and d["country"] == "中国"
    assert d["org_type"] == "地方政府" and d["official"] is True
    assert d["kind"] == "开放数据门户" and d["openness"] == "部分受限"
    assert d["note"] == "需注册" and d["api"] is True
    nbs = portal_dict(browse(conn, Filters(country="CN", domain="economy"))[0][0])
    assert nbs["name"] == "国家统计局" and nbs["name_en"] == "NBS" and nbs["featured"]
    gd = portal_dict(browse(conn, Filters(region="CN-GD"))[0][0])
    assert gd["region"] == "广东" and gd["verify"] == "ok"
