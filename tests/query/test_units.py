from datafinder.query.plan import industries_in
from datafinder.query.units import (
    Unit,
    balanced_pick,
    build_units,
    prefilter_terms,
    unit_score,
    unit_scores,
)


def test_concept_words_get_thesaurus_synonyms():
    flat = [a for u in build_units("各省研发") for a in u.alts]
    assert "研究与试验发展" in flat and "R&D" in flat


def test_full_specific_terms_survive_when_thesaurus_has_a_shorter_key():
    # "固定资产投资"含词表键"投资"：用户的具体说法不能被词表键吞掉
    units = build_units("固定资产投资与社会消费品零售总额")
    firsts = [u.alts[0] for u in units]
    assert "固定资产投资" in firsts and "社会消费品零售总额" in firsts


def test_thesaurus_key_spanning_split_concepts_replaces_the_fragments():
    units = build_units("教育资源配置")
    edu = next(u for u in units if u.alts[0] == "教育资源")
    assert "在校学生" in edu.alts
    assert "教育" not in [u.alts[0] for u in units]  # 被整体键吞掉的零碎概念不重复计分


def test_location_and_area_group_words_are_not_part_of_indicator_names():
    flat = [a for u in build_units("长三角地区经济增长") for a in u.alts]
    assert not any("长三角" in a for a in flat)
    flat = [a for u in build_units("河北省工业增加值") for a in u.alts]
    assert not any("河北" in a for a in flat) and any("工业增加值" in a for a in flat)


def test_scores_name_beats_path_beats_nothing_and_generic_words_are_ignored():
    units = [Unit(("工业增加值",))]
    assert unit_scores(units, "工业增加值 (亿元)", "国民经济核算") == [1.0]
    assert unit_scores(units, "地区生产总值", "分行业 › 工业增加值") == [0.5]
    assert unit_scores(units, "年末人口", "人口") == [0.0]
    assert build_units("统计数据分析评价") == []  # 全是泛词：不检索，免得返回垃圾


def test_combine_is_weighted_average():
    units = [Unit(("甲",), 1.0), Unit(("乙",), 0.5)]
    assert unit_score(units, "甲", "") == (1.0 * 1 + 0.5 * 0) / 1.5


def test_prefilter_terms_include_synonyms_and_bigrams_of_long_unmatched_concepts():
    terms = prefilter_terms([Unit(("研发", "研究与试验发展")), Unit(("质量体系评价",))])
    assert {"研发", "研究与试验发展", "质量", "体系", "评价"} <= set(terms)


def test_balanced_pick_guarantees_every_unit_a_representative():
    # 单元 0 的候选特别多且排在前面；单元 1 只有一个候选、排名靠后——也必须入选
    ranked = ["a1", "a2", "a3", "a4", "b1"]
    vectors = [[1, 0], [1, 0], [1, 0], [1, 0], [0, 1]]
    picked = balanced_pick(ranked, vectors, 3)
    assert "b1" in picked and len(picked) == 3
    assert picked == sorted(picked, key=ranked.index)  # 仍按原排名展示
    assert balanced_pick([], [], 3) == []


def test_industries_in_matches_substrings():
    assert "energy" in industries_in("山东省能源消费与碳排放")
    assert industries_in("xyz") == []
