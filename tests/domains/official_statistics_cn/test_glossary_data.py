"""随包的术语词典数据：结构、出处、关系与回流检索的约束。"""

import pytest

from datafinder.catalog import glossary, taxonomy
from datafinder.domains.official_statistics_cn import glossary_extract as ex
from datafinder.domains.official_statistics_cn import glossary_load
from datafinder.query.units import _thesaurus

PAGE = """
<html><body><div class="share"><p>分享到：</p></div>
<div class=TRS_Editor><div class="TRS_PreAppend">
<p class="MsoNormal"><span>　　国内生产总值（GDP）　指一个国家所有常住单位在一定时期内生产活动的最终成果。</span></p>
<p class="MsoNormal"><span>　　对于一个地区来说，称为地区生产总值。</span></p>
<p class="MsoNormal"><span>&nbsp;</span></p>
<p class="MsoNormal"><span>　　出生率（又称粗出生率）　指一定时期内出生人数与平均人数之比。计算公式为：</span></p>
<p class="MsoNormal"><span>　　（1）中央</span></p>
<p class="MsoNormal"><span>　　第一产业　指农林牧渔业。</span></p>
</div></div>
<div class="mobile"><div class="TRS_PreAppend"><p>　　国内生产总值（GDP）　手机版重复内容。</p></div></div>
</body></html>
"""
PAPER_STYLE = (
    '<div class="trs_editor_view"><p>　　批准逮捕 指人民检察院对提请逮捕的犯罪嫌疑人进行审查后依法做出的决定。</p>'
    "<p>　　其中，A 为总数。</p></div>"
)


def test_extract_takes_terms_continuations_aliases_and_ignores_the_mobile_copy():
    terms = {t["name_zh"]: t for t in ex.extract_terms(PAGE)}
    assert list(terms) == [
        "国内生产总值",
        "出生率",
        "第一产业",
    ]  # （1）中央 是分项，不是术语；手机版不重复
    gdp = terms["国内生产总值"]
    assert gdp["aliases"] == ["GDP"] and gdp["definition"].endswith("称为地区生产总值。")
    assert "手机版" not in gdp["definition"]  # 定义是原文，不夹带别的
    assert terms["出生率"]["aliases"] == ["粗出生率"]
    assert terms["出生率"]["definition"].endswith("（1）中央")  # 分项并入上一术语的定义


def test_extract_handles_the_other_page_template_with_an_ascii_space_delimiter():
    terms = ex.extract_terms(PAPER_STYLE)
    assert [t["name_zh"] for t in terms] == ["批准逮捕"]
    assert "其中，A 为总数。" in terms[0]["definition"]  # 带句读的段落是续段，不会被当成术语


def test_build_marks_official_text_with_source_and_flags_missing_formulas():
    pages = [
        {
            "key": "p",
            "title": "测试页",
            "url": "https://x/p.html",
            "file": "f",
            "topic_ids": ["cnstat.macro"],
        }
    ]
    out = ex.build(pages, {"p": PAGE}, "2026-11-21")["f"]
    gdp = next(t for t in out if t["name_zh"] == "国内生产总值")
    assert (
        gdp["verification_status"] == "official_sourced" and gdp["source_url"] == "https://x/p.html"
    )
    assert gdp["term_id"].startswith("cnstat.term.p.") and "scope_note" not in gdp
    assert (
        "图片" in next(t for t in out if t["name_zh"] == "出生率")["scope_note"]
    )  # 公式是图片，如实说明


@pytest.fixture(scope="module")
def loaded():
    return glossary_load.load()


def test_shipped_glossary_is_sourced_unique_and_topics_exist(loaded):
    terms, _ = loaded
    assert len(terms) > 300
    ids = [t["term_id"] for t in terms]
    assert len(ids) == len(set(ids))
    for t in terms:
        assert t["verification_status"] == "official_sourced" and t["source_url"].startswith(
            "https://www.stats.gov.cn/sj/zbjs/"
        )
        assert t["definition"].strip() and all(taxonomy.get(i) for i in t["topic_ids"])


def test_curated_relations_resolve_and_nothing_is_marked_reviewed_without_review(loaded):
    gdp = glossary.find_by_name("国内生产总值")[0]
    assert {"地区生产总值", "地区GDP", "GDP"} <= set(gdp.aliases)
    rel = {(r.type, glossary.get(r.target_id).name_zh) for r in gdp.relations}
    assert ("not_equivalent", "国民总收入") in rel
    cpi = glossary.find_by_name("居民消费价格指数")[0]
    assert {glossary.get(r.target_id).name_zh for r in cpi.relations if r.type == "narrower"} == {
        "城市居民消费价格指数",
        "农村居民消费价格指数",
    }
    assert not any(t.verification_status == "reviewed" for t in glossary.terms().values())


def test_only_official_abbreviations_flow_back_into_search_not_curated_aliases(loaded):
    _, search_aliases = loaded
    assert search_aliases["GDP"] == ["国内生产总值", "GDP"]
    assert "地区生产总值" not in search_aliases  # 人工补充的别名只用于查词，不改变检索
    assert _thesaurus()["GDP"] == ("国内生产总值", "GDP")


def test_hand_written_thesaurus_wins_over_glossary_aliases(loaded):
    th = _thesaurus()
    assert "研究与试验发展" in th["研发"] and th["研发"][0] == "研究与试验发展"
