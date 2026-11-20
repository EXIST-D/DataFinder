"""本领域的主题词典与规则：结构合法，并且对真实的分类名/章名/表标题给出预期标签。"""

import pytest

from datafinder.catalog import regions, taxonomy
from datafinder.catalog.rules import Target
from datafinder.domains.official_statistics_cn import DOMAIN_ID
from datafinder.query.units import _thesaurus


def rules():
    return taxonomy.rules(DOMAIN_ID)


def test_taxonomy_structure():
    ts = {i: t for i, t in taxonomy.topics().items() if t.domain == DOMAIN_ID}
    l1 = [t for t in ts.values() if t.level == 1]
    assert len(l1) == 20 and sum(1 for t in l1 if t.reserved) == 1  # 19 个有数据 + 预留的质量主题
    assert ts["cnstat.quality"].reserved
    assert all(t.id.startswith("cnstat.") for t in ts.values())
    from datafinder.portals.tag import load_config

    valid_global = set(load_config().industries)
    for t in ts.values():
        assert t.global_topics and set(t.global_topics) <= valid_global, t.id
    # 主题名里不混入机构、格式、年份（分面相互独立）
    for t in ts.values():
        assert not any(w in t.name_zh for w in ("统计局", "年鉴", "Excel", "xls", "2024")), t.id


@pytest.mark.parametrize(
    ("top", "title", "expected"),
    [
        ("工业", "按行业分规模以上工业企业主要经济指标 (2012-2022)", {"cnstat.industry", "cnstat.industry.enterprise_performance"}),
        ("十三、工 业", "规模以上工业企业主要经济指标(2024年)", {"cnstat.industry", "cnstat.industry.enterprise_performance"}),
        ("二十、科学技术", "研究与试验发展(R&D)经费", {"cnstat.innovation"}),
        ("十九篇 教育和科技", "普通高等学校基本情况", {"cnstat.education", "cnstat.innovation"}),
        ("第二十篇 文化、体育和卫生", "卫生机构数", {"cnstat.culture", "cnstat.health"}),
        ("价格指数", "居民消费价格指数 (上年=100)", {"cnstat.prices", "cnstat.prices.cpi"}),
        ("价格指数", "工业生产者出厂价格指数", {"cnstat.prices", "cnstat.prices.ppi", "cnstat.industry"}),
        ("国民经济核算", "分行业增加值", {"cnstat.macro"}),
        ("国民经济核算", "工业增加值", {"cnstat.macro", "cnstat.industry", "cnstat.industry.value_added"}),
        ("固定资产投资和房地产", "商品房销售面积", {"cnstat.investment"}),
        ("就业人员和工资", "城镇单位就业人员平均工资", {"cnstat.livelihood"}),
        ("采购经理指数", "制造业采购经理指数", {"cnstat.macro", "cnstat.industry"}),
        ("物价", "居民消费价格分类指数", {"cnstat.prices", "cnstat.prices.cpi"}),
    ],
)
def test_real_category_and_title_names_map_to_expected_topics(top, title, expected):
    got = set(rules().topics(Target(top=top, path=[top], title=title)))
    assert expected <= got
    assert got - expected <= {"cnstat.industry.value_added", "cnstat.industry.enterprise_performance"}


def test_chapters_that_are_really_a_region_set_the_region_facet():
    hk = rules().regions(Target(top="二十六、香港特别行政区主要社会经济指标"))
    assert [(t.region, t.relation) for t in hk] == [("CN-HK", "covers")]
    assert rules().regions(Target(top="附录、国际主要社会经济指标"))[0].region == "WORLD"
    county = rules().regions(Target(top="第二十二篇 各县(市、区)主要经济指标", title="某县指标"))
    assert county and county[0].granularity == "county" and county[0].region is None
    prefecture = rules().regions(Target(title="各市规模以上工业增加值"))
    assert prefecture[0].granularity == "prefecture"
    for tag in hk + county + prefecture:
        assert tag.evidence_status == "inferred"
        assert tag.region is None or regions.get(tag.region) is not None  # 规则里的区域必须存在


def test_every_thesaurus_related_term_is_a_plausible_statistics_name():
    assert "研究与试验发展" in _thesaurus()["研发"]
