import pytest

from datafinder.portals.search import search_portals
from datafinder.portals.sync import Portal, apply_curated, write_db
from datafinder.store import connect


def P(id, name, url, **kw):
    kw.setdefault("source", "dateno")
    return Portal(id=id, name=name, url=url, **kw)


PORTALS = [
    P(
        "nbs",
        "National Data NBS China",
        "https://data.stats.gov.cn",
        country="CN",
        catalog_type="Indicators catalog",
        industries=["statistics", "economy", "population"],
        featured=True,
        is_national=True,
        description="Statistical data of China",
    ),
    P(
        "cma",
        "China Meteorological Data Service",
        "https://data.cma.cn",
        country="CN",
        catalog_type="Scientific data repository",
        industries=["climate"],
        featured=True,
        description="Weather and climate data",
    ),
    P(
        "ln",
        "Liaoning Open Data Platform",
        "https://data.ln.gov.cn",
        country="CN",
        region="CN-LN",
        catalog_type="Open data portal",
        industries=["government"],
        description="Provincial open data platform",
    ),
    P(
        "sd",
        "Shandong Open Data Platform",
        "https://data.sd.gov.cn",
        country="CN",
        region="CN-SD",
        catalog_type="Open data portal",
        industries=["government"],
        featured=True,
        description="Provincial open data platform",
    ),
    P(
        "census",
        "US Census Bureau",
        "https://data.census.gov",
        country="US",
        catalog_type="Open data portal",
        industries=["population", "statistics"],
        description="Population census data",
    ),
    P(
        "jma",
        "Japan Meteorological Agency",
        "https://www.jma.go.jp",
        country="JP",
        catalog_type="Scientific data repository",
        industries=["climate"],
        description="Weather observations",
    ),
]


@pytest.fixture()
def conn():
    c = connect(":memory:")
    write_db(c, [*PORTALS], {"registry_ref": "test"})
    return c


def ids(hits):
    return [h.row["id"] for h in hits]


def test_two_char_chinese_word_matches_via_hints(conn):
    # "气象" 只出现在中文检索提示（气象气候行业词）里，英文名称里没有
    _, hits = search_portals(conn, "气象")
    assert "cma" in ids(hits) and "jma" in ids(hits)
    assert ids(hits)[0] == "cma"  # 精选 + 中文查询偏中国


def test_english_alternative_expansion_hits_english_text(conn):
    # "人口普查" → 同时用 "population census" 去匹配英文描述
    _, hits = search_portals(conn, "人口普查")
    by_id = {h.row["id"]: h for h in hits}
    assert by_id["census"].coverage == 1.0  # 字面命中（靠英文扩展词）
    assert by_id["nbs"].coverage < 1.0  # 仅靠行业标签推断，权重减半


def test_region_beats_featured_other_province(conn):
    _, hits = search_portals(conn, "辽宁 开放数据")
    assert ids(hits)[0] == "ln"  # 山东是精选，但省份明确时本省优先


def test_industry_tag_counts_as_concept_match(conn):
    # NBS 描述里没有 GDP / 人口，但行业标签是 宏观经济 / 人口社会
    plan, hits = search_portals(conn, "中国 人口 GDP")
    assert ids(hits)[0] == "nbs"
    top = hits[0]
    assert set(top.matched) == {"人口", "gdp"}


def test_filters_only_query_lists_featured_first(conn):
    _, hits = search_portals(conn, "", country="CN")
    assert ids(hits)[:2] == ["nbs", "cma"] or set(ids(hits)[:3]) >= {"nbs", "cma"}
    assert all(h.row["country"] == "CN" for h in hits)


def test_unknown_word_returns_nothing(conn):
    _, hits = search_portals(conn, "qwertyuiop")
    assert hits == []


def test_curated_annotates_by_normalized_link_and_adds_missing():
    portals = [P("a", "A", "http://www.example.org/x/")]
    entries = [
        {
            "link": "https://example.org/x",
            "name_zh": "示例",
            "industries": ["economy"],
            "featured": True,
        },
        {
            "add": True,
            "link": "https://new.example.org",
            "name": "New",
            "name_zh": "新",
            "country": "CN",
        },
        {"link": "https://nowhere.example.org"},
    ]
    applied, added, missing = apply_curated(portals, entries)
    assert (applied, added, missing) == (2, 1, ["https://nowhere.example.org"])
    first, second = portals
    assert first.name_zh == "示例" and first.featured
    assert first.source == "dateno+curated"
    assert second.source == "curated" and second.curated
