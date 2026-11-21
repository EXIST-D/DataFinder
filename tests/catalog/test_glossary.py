import pytest

from datafinder.catalog import glossary as g


def raw(i, name, **kw):
    return {
        "term_id": f"t.{i}",
        "name_zh": name,
        "definition": kw.pop("definition", f"{name}的定义。"),
        "topic_ids": kw.pop("topic_ids", ["cnstat.macro"]),
        "verification_status": kw.pop("verification_status", "draft"),
        **kw,
    }


@pytest.fixture
def lex(monkeypatch):
    """用一个独立的领域 id 登记，测完还原，不影响真实词典。"""
    from datafinder import domains

    domains.load_all()  # 先让领域包登记完，再临时替换，否则它的登记会落进临时表而丢失
    monkeypatch.setattr(g, "_TERMS", {})
    return g


def test_validation_rejects_terms_without_provenance_or_with_bad_fields(lex):
    with pytest.raises(ValueError, match="source_url"):
        g.register_glossary("d", [raw(1, "甲", verification_status="official_sourced")])
    with pytest.raises(ValueError, match="verification_status"):
        g.register_glossary("d", [raw(1, "甲", verification_status="maybe")])
    with pytest.raises(ValueError, match="definition"):
        g.register_glossary("d", [raw(1, "甲", definition=" ")])
    with pytest.raises(ValueError, match="关系类型"):
        g.register_glossary("d", [raw(1, "甲", relations=[{"type": "same", "target_id": "t.2"}])])
    with pytest.raises(ValueError, match="不存在"):
        g.register_glossary(
            "d", [raw(1, "甲", relations=[{"type": "related", "target_id": "t.9"}])]
        )
    with pytest.raises(ValueError, match="重复"):
        g.register_glossary("d", [raw(1, "甲"), raw(1, "乙")])


def test_relations_get_their_inverse_and_symmetric_sides(lex):
    g.register_glossary(
        "d",
        [
            raw(
                1,
                "居民消费价格指数",
                relations=[{"type": "narrower", "target_id": "t.2", "note": "n"}],
            ),
            raw(2, "城市居民消费价格指数"),
            raw(3, "国内生产总值", relations=[{"type": "not_equivalent", "target_id": "t.4"}]),
            raw(4, "国民总收入"),
        ],
    )
    assert [(r.type, r.target_id) for r in g.get("t.2").relations] == [("broader", "t.1")]
    assert g.get("t.2").relations[0].note == "n"
    assert [(r.type, r.target_id) for r in g.get("t.4").relations] == [("not_equivalent", "t.3")]


def test_search_ranks_exact_then_prefix_then_contains_then_definition(lex):
    g.register_glossary(
        "d",
        [
            raw(1, "国内生产总值", aliases=["GDP"]),
            raw(2, "国内生产总值指数"),
            raw(3, "人均国内生产总值"),
            raw(4, "某指标", definition="与国内生产总值之比"),
        ],
    )
    total, items = g.search("国内生产总值")
    assert [t.term_id for t in items] == ["t.1", "t.2", "t.3", "t.4"] and total == 4
    assert [t.term_id for t in g.search("gdp")[1]] == ["t.1"]  # 别名、大小写不敏感
    assert g.search("国内生产总值", status="reviewed")[0] == 0


def test_topic_filter_includes_sub_topics_and_name_lookup_can_be_ambiguous(lex):
    g.register_glossary(
        "d",
        [
            raw(1, "工业增加值", topic_ids=["cnstat.industry.value_added"]),
            raw(2, "死亡率", topic_ids=["cnstat.population"]),
            raw(3, "死亡率", topic_ids=["cnstat.health"]),
        ],
    )
    assert [t.term_id for t in g.search(topic_id="cnstat.industry")[1]] == ["t.1"]  # 含下级主题
    assert {t.term_id for t in g.find_by_name("死亡率")} == {
        "t.2",
        "t.3",
    }  # 同名多条都给，不替用户挑


def test_match_text_prefers_longer_names_and_skips_overlaps_and_short_names(lex):
    g.register_glossary(
        "d",
        [
            raw(1, "国内生产总值"),
            raw(2, "生产总值"),  # 被更长的名称覆盖，不重复
            raw(3, "工业"),  # 两个字太泛
            raw(4, "能源消费总量"),
        ],
    )
    assert [t.term_id for t in g.match_text("河北省国内生产总值与能源消费总量的关系，工业")] == [
        "t.1",
        "t.4",
    ]
