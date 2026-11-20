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
