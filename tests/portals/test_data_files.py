"""数据文件完整性：词表/行业/地区/精选清单加一行就能生效，所以要防止写错。"""

from datafinder.portals.tag import load_config
from datafinder.query.lexicon import load_lexicon
from datafinder.resources import load_yaml


def test_lexicon_loads_and_has_core_entries():
    lex = load_lexicon()
    assert len(lex.zh2en) >= 300
    assert "人口普查" in lex.zh2en and "population census" in lex.zh2en["人口普查"]
    assert "数据" in lex.weak and "的" in lex.stop
    # 键必须是字符串（YAML 的 NO/ON/YES 陷阱）
    assert all(isinstance(k, str) and k for k in lex.zh2en)
    assert all(vals and all(isinstance(v, str) for v in vals) for vals in lex.zh2en.values())


def test_regions_keys_are_strings():
    regions = load_yaml("regions.yaml")
    assert "NO" in regions["countries"]
    assert all(isinstance(k, str) for k in regions["countries"])
    assert len(regions["provinces"]) == 31


def test_industry_references_are_valid():
    cfg = load_config()
    valid = set(cfg.industries)
    for topic, inds in cfg.topic_map.items():
        assert set(inds) <= valid, topic
    for entry in load_yaml("curated", "portals.yaml"):
        assert set(entry.get("industries", [])) <= valid, entry["link"]


def test_curated_entries_are_well_formed():
    entries = load_yaml("curated", "portals.yaml")
    links = [e["link"] for e in entries]
    assert len(links) == len(set(links)), "精选清单里有重复链接"
    for e in entries:
        assert e["link"].startswith("http")
        assert e.get("name_zh"), e["link"]
        if e.get("add"):
            assert {"name", "country", "catalog_type"} <= set(e), e["link"]


def test_thesaurus_entries_are_well_formed():
    th = load_yaml("thesaurus.zh.yaml")
    assert len(th) >= 30
    for key, syns in th.items():
        assert isinstance(key, str) and len(key) >= 2, key
        assert isinstance(syns, list) and syns and all(isinstance(s, str) and s for s in syns), key
        assert len(syns) == len(set(syns)), f"{key} 的相关词有重复"
        assert len(syns) <= 8, f"{key} 的相关词太多，会把无关指标拉进来"


def test_yearbook_registry_regions_exist():
    from datafinder.yearbook.registry import load_series

    provinces = set(load_yaml("regions.yaml")["provinces"])
    for s in load_series():
        assert s.region == "CN" or s.region in provinces, s.id
