from datetime import date

from datafinder.query.plan import parse

TODAY = date(2026, 11, 20)


def labels(plan):
    return [c.label for c in plan.concepts]


def test_user_example_query():
    plan = parse("中国各省近十年人口 GDP 城镇化率 CSV", today=TODAY)
    assert plan.lang == "zh"
    assert plan.countries == ["CN"]
    assert plan.formats == ["csv"]
    assert plan.years == (2016, 2026)
    assert labels(plan) == ["各省", "人口", "gdp", "城镇化率"]
    assert {"population", "economy"} <= set(plan.industries)


def test_concepts_carry_english_alternatives():
    plan = parse("人口普查", today=TODAY)
    alts = [" ".join(a) for a in plan.concepts[0].alts]
    assert alts[0] == "人口 口普 普查"
    assert "population census" in alts


def test_province_implies_country():
    plan = parse("辽宁 开放数据", today=TODAY)
    assert plan.regions == ["CN-LN"]
    assert plan.countries == ["CN"]
    assert labels(plan) == ["开放数据"]


def test_longest_country_name_wins():
    assert parse("印度尼西亚 人口", today=TODAY).countries == ["ID"]
    assert parse("印度 人口", today=TODAY).countries == ["IN"]


def test_norway_yaml_key_is_not_parsed_as_boolean():
    # YAML 里裸写 NO 会变成 False；这里确保已加引号
    assert parse("挪威 能源", today=TODAY).countries == ["NO"]


def test_weak_words_dropped_when_real_words_exist():
    assert labels(parse("人口数据", today=TODAY)) == ["人口"]


def test_only_weak_words_are_kept():
    assert labels(parse("数据", today=TODAY)) == ["数据"]


def test_unknown_chinese_run_is_kept_as_literal():
    plan = parse("饕餮纹样", today=TODAY)
    assert plan.concepts and plan.concepts[0].alts[0] == ("饕餮", "餮纹", "纹样")


def test_year_range_and_recent():
    assert parse("2010年到2020年 人口", today=TODAY).years == (2010, 2020)
    assert parse("近5年 GDP", today=TODAY).years == (2021, 2026)


def test_english_query():
    plan = parse("air quality csv", today=TODAY)
    assert plan.lang == "en"
    assert plan.formats == ["csv"]
    assert labels(plan) == ["air", "quality"]


def test_fts_match_quotes_phrases():
    match = parse("人口", today=TODAY).fts_match()
    assert '"人口"' in match and '"population"' in match and " OR " in match


def test_gdp_in_chinese_is_not_swallowed_by_a_location_alias():
    # “国内”曾是中国的别名，会把“国内生产总值”剥成“生产总值”
    plan = parse("国内生产总值", today=TODAY)
    assert labels(plan) == ["国内生产总值"]
    assert plan.countries == []
    assert plan.query_en() == "gdp"


def test_latin_concept_gets_chinese_alternatives():
    plan = parse("GDP", today=TODAY)
    alts = [" ".join(a) for a in plan.concepts[0].alts]
    assert "国内 内生 生产 产总 总值" in alts  # gdp → 国内生产总值，中文记录才能被命中


def test_dimension_words_are_flagged_and_left_out_of_api_queries():
    plan = parse("中国各省人口 GDP", today=TODAY)
    assert [c.label for c in plan.concepts if c.dimension] == ["各省"]
    assert plan.query_en() == "population gdp china"
    assert "各省" not in plan.zh_terms()


def test_query_builders_cap_terms_and_add_location():
    plan = parse("中国各省近十年人口 GDP 城镇化率 CSV", today=TODAY)
    assert plan.query_en(max_terms=2) == "population gdp china"
    assert plan.query_en(max_terms=1) == "population china"
    assert plan.zh_terms() == ["人口", "国内生产总值", "城镇化率"]


def test_english_only_query_has_no_chinese_terms_unless_mappable():
    assert parse("zebra giraffe", today=TODAY).zh_terms() == []
    assert parse("population", today=TODAY).zh_terms() == ["人口"]


def test_synonym_concepts_are_merged():
    # 词表里 居民消费价格指数 的说法已含 cpi，单独的 CPI 是同义重复，不能让它拉低中文记录的覆盖度
    assert labels(parse("居民消费价格指数 CPI", today=TODAY)) == ["居民消费价格指数"]
    assert labels(parse("CPI 居民消费价格指数", today=TODAY)) == ["居民消费价格指数"]


def test_unrelated_concepts_are_not_merged():
    assert labels(parse("人口 GDP", today=TODAY)) == ["人口", "gdp"]


def test_city_is_a_dimension_not_a_search_term():
    plan = parse("城市房价", today=TODAY)
    assert [c.label for c in plan.concepts if c.dimension] == ["城市"]
    assert plan.query_en() == "house price"  # 不带 city，避免 AND 检索把结果搜没
    assert plan.zh_terms() == ["房价"]  # 也不把"城市"单独发给国家统计局（会带回"城市绿地面积"）
