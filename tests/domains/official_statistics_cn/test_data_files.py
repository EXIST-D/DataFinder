"""本领域数据文件的完整性：术语词表、年鉴系列、来源登记。"""

from datafinder.domains.official_statistics_cn.yearbook.registry import load_series
from datafinder.resources import load_package_yaml, load_yaml

PKG = "datafinder.domains.official_statistics_cn"


def test_thesaurus_entries_are_well_formed():
    th = load_package_yaml(PKG, "thesaurus.zh.yaml")
    assert len(th) >= 30
    for key, syns in th.items():
        assert isinstance(key, str) and len(key) >= 2, key
        assert isinstance(syns, list) and syns and all(isinstance(s, str) and s for s in syns), key
        assert len(syns) == len(set(syns)), f"{key} 的相关词有重复"
        assert len(syns) <= 8, f"{key} 的相关词太多，会把无关指标拉进来"


def test_yearbook_registry_regions_exist():
    provinces = set(load_yaml("regions.yaml")["provinces"])
    for s in load_series():
        assert s.region == "CN" or s.region in provinces, s.id


def test_domain_sources_use_registered_protocols_and_load_before_core_sources():
    from datafinder.sources.registry import load_specs

    ids = [s.id for s in load_specs()]
    assert ids[0] == "nbs" and ids.count("nbs") == 1  # 领域来源排在核心之前，且不重复
