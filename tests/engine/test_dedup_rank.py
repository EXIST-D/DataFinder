from datetime import date

from datafinder.engine.dedup import identity_keys, merge, normalize_doi, normalize_link
from datafinder.engine.rank import rank
from datafinder.models import Dataset, Resource
from datafinder.query.plan import parse


def D(title, url, source, **kw):
    return Dataset(title=title, url=url, source=source, **kw)


# ───────────── 去重 ─────────────


def test_normalize_doi_handles_resolver_urls_and_case():
    assert normalize_doi("https://doi.org/10.5281/ZENODO.123") == "10.5281/zenodo.123"
    assert normalize_doi("doi:10.5281/zenodo.123") == "10.5281/zenodo.123"
    assert normalize_doi("no doi here") is None


def test_normalize_link_drops_scheme_www_fragment_slash_and_utm():
    a = normalize_link("https://www.Example.org/a/b/?utm_source=x&id=1#frag")
    b = normalize_link("http://example.org/a/b?id=1")
    assert a == b == "example.org/a/b?id=1"


def test_same_doi_merges_across_sources_and_first_wins():
    z = D(
        "Population of China",
        "https://zenodo.org/records/1",
        "zenodo",
        doi="10.5281/zenodo.1",
        license="cc-by-4.0",
        description="from zenodo",
    )
    dc = D(
        "Population of China",
        "https://doi.org/10.5281/zenodo.1",
        "datacite",
        doi="10.5281/ZENODO.1",
        description="from datacite",
        publisher="CERN",
    )
    merged, ranks = merge({"zenodo": [z], "datacite": [dc]})
    assert len(merged) == 1
    m = merged[0]
    assert m.description == "from zenodo"  # 先到者优先
    assert m.publisher == "CERN"  # 后到者补缺
    assert ranks[0] == {"zenodo": 0, "datacite": 0}


def test_concept_doi_alias_folds_versions():
    v1 = D(
        "Data v1",
        "https://a.org/1",
        "zenodo",
        doi="10.5281/zenodo.11",
        aliases=["10.5281/zenodo.10"],
    )
    concept = D("Data (all versions)", "https://b.org/x", "datacite", doi="10.5281/zenodo.10")
    merged, _ = merge({"zenodo": [v1], "datacite": [concept]})
    assert len(merged) == 1


def test_title_key_only_merges_across_sources():
    a1 = D("Quarterly GDP statistics 2020", "https://a.org/1", "zenodo", publisher="X")
    a2 = D("Quarterly GDP statistics 2020", "https://a.org/2", "zenodo", publisher="X")
    b = D("Quarterly GDP statistics 2020", "https://b.org/1", "datacite", publisher="X")
    merged, _ = merge({"zenodo": [a1, a2], "datacite": [b]})
    # 同源两条不因标题合并；但它们都会被另一来源的同名记录连起来（传递性）
    assert len(merged) == 1


def test_same_source_same_title_stays_separate():
    a1 = D("Quarterly GDP statistics 2020", "https://a.org/1", "zenodo", publisher="X")
    a2 = D("Quarterly GDP statistics 2020", "https://a.org/2", "zenodo", publisher="X")
    merged, _ = merge({"zenodo": [a1, a2]})
    assert len(merged) == 2


def test_chinese_titles_do_not_collide():
    # Mobus 用 [^a-z0-9] 清洗标题，导致所有中文标题碰成同一个键；这里必须保持区分
    a = D("国内生产总值累计值 (亿元)", "https://s.cn/?cid=1", "nbs", publisher="国家统计局")
    b = D("人口数 (人口抽样调查) (人)", "https://s.cn/?cid=2", "nbs", publisher="国家统计局")
    c = D("国内生产总值累计值 (亿元)", "https://other.cn/x", "datacite", publisher="国家统计局")
    keys_a = identity_keys(a)[1]
    keys_b = identity_keys(b)[1]
    assert keys_a != keys_b
    merged, _ = merge({"nbs": [a, b], "datacite": [c]})
    assert len(merged) == 2  # a 与 c 合并（跨源同名同发布方），b 独立


def test_short_titles_are_not_used_as_keys():
    a = D("Data", "https://a.org/1", "zenodo")
    b = D("Data", "https://b.org/1", "datacite")
    assert identity_keys(a)[1] == []
    assert len(merge({"zenodo": [a], "datacite": [b]})[0]) == 2


def test_resources_union_by_url():
    r1 = Resource("file", "a.csv", "https://x/a.csv", "CSV")
    r2 = Resource("file", "b.csv", "https://x/b.csv", "CSV")
    a = D("Same dataset title here", "https://a.org/1", "zenodo", doi="10.1/x", resources=[r1])
    b = D(
        "Same dataset title here", "https://b.org/1", "datacite", doi="10.1/x", resources=[r1, r2]
    )
    merged, _ = merge({"zenodo": [a], "datacite": [b]})
    assert [r.url for r in merged[0].resources] == ["https://x/a.csv", "https://x/b.csv"]


# ───────────── 排序 ─────────────

TODAY = date(2026, 11, 20)


def run_rank(items, query, official=frozenset()):
    merged, ranks = merge({"s": items} if isinstance(items, list) else items)
    return rank(merged, ranks, parse(query, today=TODAY), official=official)


def test_chinese_record_is_not_dropped_for_chinese_query():
    zh = D("中国各省人口普查数据", "https://a.cn/1", "s", description="按省份统计的人口数据")
    out = run_rank([zh], "人口普查")
    assert [d.title for d in out] == ["中国各省人口普查数据"]


def test_english_record_is_recalled_by_chinese_query_via_expansion():
    en = D("China population census 2020", "https://a.org/1", "s", description="census tables")
    assert len(run_rank([en], "人口普查")) == 1


def test_described_record_matching_nothing_is_dropped():
    noise = D("Bird migration routes", "https://a.org/1", "s", description="birds and flyways")
    good = D("Population census tables", "https://a.org/2", "s", description="census")
    out = run_rank([noise, good], "population census")
    assert [d.title for d in out] == ["Population census tables"]


def test_title_match_beats_description_only_match():
    t = D("Air quality monitoring", "https://a.org/1", "s", description="stations")
    d = D(
        "Station records 2020", "https://a.org/2", "s", description="includes air quality readings"
    )
    out = run_rank({"s": [d, t]}, "air quality")  # d 名次更靠前，但 t 标题命中
    assert out[0].title == "Air quality monitoring"


def test_agreement_between_sources_boosts_rank():
    shared_a = D("Urban population by city", "https://x.org/1", "a", doi="10.1/shared")
    shared_b = D("Urban population by city", "https://y.org/1", "b", doi="10.1/shared")
    solo = D("Urban population statistics", "https://z.org/1", "a")
    out = run_rank({"a": [solo, shared_a], "b": [shared_b]}, "urban population")
    assert out[0].doi == "10.1/shared"
    assert out[0].explain["来源数"] == 2


def test_official_and_chinese_boost_for_chinese_query():
    off = D("国内生产总值 (亿元)", "https://s.cn/1", "nbs", description="国家统计局")
    out = run_rank({"nbs": [off]}, "国内生产总值", official=frozenset({"nbs"}))
    assert out[0].explain["官方来源"] > 1 and out[0].explain["中文资源"] > 1
