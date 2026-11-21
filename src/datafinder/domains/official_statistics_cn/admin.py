"""地级、县级行政区划：登记进地域分面（省级以上由核心内置）。

数据来自民政部《县级以上行政区划代码》（data/admin_divisions.yaml，文件头写明版本与截至日期）。
区域 id 为 `CN-<6 位代码>`；县级的上级是所在地级市，省直辖县级市与直辖市的区县直接挂在省/直辖市下。
"""

from __future__ import annotations

from datafinder.catalog import regions
from datafinder.resources import load_package_yaml

from . import DOMAIN_ID


def load_regions() -> list[regions.Region]:
    data = load_package_yaml(f"datafinder.domains.{DOMAIN_ID}", "admin_divisions.yaml")
    province_of = {r.gb: r.id for r in regions.regions().values() if r.level == "province" and r.gb}
    prefectures: dict[str, str] = data["prefectures"]
    out: list[regions.Region] = []
    for code, name in prefectures.items():
        parent = province_of.get(f"{code[:2]}0000")
        if parent is None:
            continue
        alias = (name[:-1],) if name.endswith("市") and len(name) > 2 else ()
        out.append(
            regions.Region(f"CN-{code}", name, "prefecture", parent, alias, code, f"{code}000000")
        )
    for code, name in data["counties"].items():
        city = f"{code[:4]}00"
        parent = f"CN-{city}" if city in prefectures else province_of.get(f"{code[:2]}0000")
        if parent is None:
            continue
        out.append(regions.Region(f"CN-{code}", name, "county", parent, (), code, f"{code}000000"))
    return out


def register() -> None:
    regions.register_regions(load_regions())
