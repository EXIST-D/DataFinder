from datafinder.links import build_links
from datafinder.resources import load_yaml


def flat(groups):
    return {link["id"]: link for g in groups for link in g["links"]}


def test_every_template_is_well_formed():
    cfg = load_yaml("links.yaml")
    group_ids = {g["id"] for g in cfg["groups"]}
    ids = [item["id"] for item in cfg["links"]]
    assert len(ids) == len(set(ids)), "检索入口 id 重复"
    for item in cfg["links"]:
        assert item["group"] in group_ids, item["id"]
        assert item["url"].startswith("https://"), item["id"]
        assert item["check"] in {"ok", "bot", "nokey"}, item["id"]
        assert item["lang"] in {"en", "zh", "any"}, item["id"]
        if item["check"] == "nokey":
            assert "{q}" not in item["url"], f"{item['id']} 标了无关键词参数，不该有 {{q}}"
        else:
            assert item["url"].count("{q}") == 1, item["id"]


def test_english_engines_get_the_translated_english_query():
    links = flat(build_links("人口普查"))
    assert links["dateno"]["query"] == "population census"
    assert "query=population%20census" in links["dateno"]["url"]
    assert "q=population%20census" in links["zenodo"]["url"]


def test_chinese_engines_keep_the_original_query_percent_encoded():
    links = flat(build_links("人口普查"))
    assert links["baidu-files"]["query"] == "人口普查"
    assert "wd=%E4%BA%BA%E5%8F%A3%E6%99%AE%E6%9F%A5%20filetype%3Axls" in links["baidu-files"]["url"]
    assert "q=%E4%BA%BA%E5%8F%A3%E6%99%AE%E6%9F%A5" in links["scidb"]["url"]  # lang=any 用原句


def test_special_characters_cannot_break_out_of_the_url():
    links = flat(build_links("a&b=c#d"))
    # 用原句的引擎：所有特殊字符都被百分号编码，无法截断或注入额外参数
    scidb = links["scidb"]["url"]
    assert scidb == "https://www.scidb.cn/en/list?q=a%26b%3Dc%23d" and "#" not in scidb
    # 用翻译词的引擎：先分词，标点本身就被剥掉了
    assert links["dateno"]["url"] == "https://dateno.io/search?query=a%20b%20c%20d"


def test_nokey_links_have_no_query_and_carry_a_note():
    links = flat(build_links("人口"))
    assert links["nbs"]["query"] is None and "没有关键词参数" in links["nbs"]["note"]
    assert (
        flat(build_links("人口"))["zenodo"]["note"]
        and flat(build_links("人口"))["dateno"]["note"] is None
    )


def test_empty_query_still_returns_navigable_links():
    groups = build_links("")
    assert groups and all(link["url"].startswith("https://") for g in groups for link in g["links"])
