from datafinder.catalog import regions


def test_official_divisions_are_registered_with_hierarchy_and_unique_names():
    all_regions = regions.regions()
    prefectures = [r for r in all_regions.values() if r.level == "prefecture"]
    counties = [r for r in all_regions.values() if r.level == "county"]
    assert 330 <= len(prefectures) <= 340 and 2800 <= len(counties) <= 2900
    assert regions.resolve("石家庄") == "CN-130100" and regions.resolve("130102") == "CN-130102"
    assert regions.ancestors("CN-130102")[:2] == ["CN-130100", "CN-HE"]
    assert regions.resolve("朝阳区") is None  # 北京、长春等都有朝阳区：重名不猜
    # 直辖市的区挂在直辖市下；省直辖县级市挂在省下
    assert regions.get("CN-110101").parent_id == "CN-BJ"
    assert regions.get("CN-429004").parent_id == "CN-HB"  # 仙桃市
    assert regions.resolve("吉林") == "CN-JL"  # 省名优先于同名地级市的简称
    assert all(r.parent_id in all_regions for r in prefectures + counties)  # 没有悬空的上级
