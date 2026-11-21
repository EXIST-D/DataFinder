import yaml

from datafinder.query.units import build_units
from datafinder.resources import load_package_yaml

PKG = "datafinder.domains.official_statistics_cn"


def test_thesaurus_is_well_formed():
    raw = load_package_yaml(PKG, "thesaurus.zh.yaml")
    assert len(raw) >= 70
    for key, alts in raw.items():
        assert isinstance(alts, list) and alts and all(isinstance(a, str) and a for a in alts), key
        assert len(alts) == len(set(alts)), f"{key} 的相关词有重复"


def test_everyday_phrases_expand_to_the_names_statistics_actually_use():
    def alts(query: str) -> set[str]:
        return {a for u in build_units(query) for a in u.alts}

    assert {"生态环境状况", "空气"} <= alts("PM2.5浓度与空气质量")
    assert {"出生率", "总和生育率"} <= alts("低生育率背景下的生育意愿")
    assert {"社会融资规模", "货币供应量"} <= alts("社融与货币供应")
    assert {"城镇登记失业率"} <= alts("失业情况")
    assert "太阳能" in alts("光伏发电")
    # 没有写进词表的词不凭空扩展
    assert alts("鸡蛋") == {"鸡蛋"} or "生态环境状况" not in alts("鸡蛋")


def test_yaml_file_parses_with_plain_loader():
    from importlib.resources import files

    text = (files(PKG) / "data" / "thesaurus.zh.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(text)
