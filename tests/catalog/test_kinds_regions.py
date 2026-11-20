import pytest

from datafinder.catalog import kinds, regions


def test_kinds_keep_what_it_is_apart_from_how_to_get_it():
    ks = kinds.kinds()
    assert {"portal", "database", "dataset", "table", "indicator", "category"} <= set(ks)
    assert "api" not in ks and "csv" not in ks  # 访问方式、文件格式不是资源类型
    assert "category" not in kinds.searchable_kinds() and "indicator" in kinds.searchable_kinds()
    assert {"series", "edition", "dataset", "database"} <= kinds.container_kinds()


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("direct_file", "download"),
        ("api", "generate"),
        ("html_parse", "generate"),
        ("page", "link"),
        ("manual", "link"),
    ],
)
def test_delivery_is_derived_from_access_method(method, expected):
    assert kinds.delivery_of(method) == expected


def test_best_delivery_prefers_download_then_generate_then_link():
    assert kinds.best_delivery(["page", "api", "direct_file"]) == "download"
    assert kinds.best_delivery(["manual", "api"]) == "generate"
    assert kinds.best_delivery(["manual"]) == "link"
    assert kinds.best_delivery([]) == "link"  # 没有分发入口：只能给链接


def test_enums_have_no_duplicates():
    for values in (
        kinds.file_formats(),
        kinds.usabilities(),
        kinds.verification_levels(),
        kinds.access_statuses(),
    ):
        assert len(values) == len(set(values))


@pytest.mark.parametrize("value", ["CN-HE", "河北", "河北省", "130000", "130000000000", " 河北 "])
def test_province_resolves_from_id_name_alias_and_both_code_systems(value):
    assert regions.resolve(value) == "CN-HE"


def test_regions_have_hierarchy_and_both_codes():
    r = regions.get("CN-HE")
    assert r and r.gb == "130000" and r.stat12 == "130000000000" and r.parent_id == "CN"
    assert regions.ancestors("CN-HE") == ["CN", "WORLD"]
    assert "CN-HE" in regions.descendants("CN") and "CN-HK" in regions.descendants("CN")
    assert regions.name_zh("CN-HK") == "香港" and regions.name_zh("???") == "???"


def test_unknown_names_are_not_guessed_and_special_regions_are_not_provinces():
    assert regions.resolve("火星") is None and regions.resolve("") is None
    assert regions.get("CN-HK").level == "special"
    assert sum(1 for r in regions.regions().values() if r.level == "province") == 31


def test_all_provinces_have_unique_gb_codes():
    gbs = [r.gb for r in regions.regions().values() if r.level in ("province", "special")]
    assert len(gbs) == 34 and len(set(gbs)) == 34
