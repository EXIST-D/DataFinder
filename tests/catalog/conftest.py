"""目录测试的合成数据：国家统计局式（库→数据集→指标）与年鉴式（系列→版次→表）两种形态。"""

import pytest

from datafinder.catalog import Catalog, Distribution, Entry, RegionLink, TopicLink, make_id

NBS, YB = "nbs", "yb.sd"


def topics(*ids):
    return [TopicLink(i, "rule") for i in ids]


def build(cat: Catalog) -> Catalog:
    cat.upsert_source(NBS, domain_id="official_statistics_cn", adapter_id="nbs", name="国家统计局")
    cat.upsert_source(
        YB, domain_id="official_statistics_cn", adapter_id="yearbook", name="山东统计年鉴"
    )
    prov = [RegionLink("CN", "covers", "province", "descendants", "structure")]
    cat.upsert(
        [
            Entry(
                NBS,
                "database",
                "6",
                "分省年度数据",
                regions=prov,
                frequency="annual",
                distributions=[Distribution("page", "https://x/db6")],
            ),
            Entry(
                NBS,
                "dataset",
                "6/c1",
                "地区生产总值",
                parent_id=make_id(NBS, "database", "6"),
                path="分省年度数据 › 国民经济核算",
                period_start=1990,
                frequency="annual",
                topics=topics("cnstat.macro"),
                regions=prov,
                distributions=[Distribution("api", "https://x/es", usability="tabular")],
            ),
            Entry(
                NBS,
                "indicator",
                "6/c1/i1",
                "工业增加值",
                unit="亿元",
                parent_id=make_id(NBS, "dataset", "6/c1"),
                path="分省年度数据 › 国民经济核算 › 地区生产总值",
                period_start=1990,
                frequency="annual",
                topics=topics("cnstat.macro", "cnstat.industry", "cnstat.industry.value_added"),
                regions=prov,
                distributions=[Distribution("api", "https://x/es", usability="tabular")],
            ),
            Entry(
                NBS,
                "indicator",
                "6/c1/i2",
                "第二产业增加值",
                unit="亿元",
                parent_id=make_id(NBS, "dataset", "6/c1"),
                path="分省年度数据 › 国民经济核算 › 地区生产总值",
                period_start=1990,
                frequency="annual",
                topics=topics("cnstat.macro"),
                regions=prov,
                distributions=[Distribution("api", "https://x/es")],
            ),
            Entry(
                NBS,
                "indicator",
                "3/c9/i1",
                "工业增加值",
                unit="亿元",
                path="年度数据 › 国民经济核算 › 分行业增加值",
                period_start=1952,
                period_end=2023,
                frequency="annual",
                topics=topics("cnstat.macro", "cnstat.industry"),
                regions=[RegionLink("CN", "covers", "country", "exact", "structure")],
                distributions=[Distribution("api", "https://x/es", usability="tabular")],
            ),
            # 年鉴：山东，同一张表两个版次 + 一张只有链接的表
            Entry(
                YB,
                "series",
                "CN-SD",
                "山东统计年鉴",
                regions=[RegionLink("CN-SD", "covers", None, "exact", "structure")],
                distributions=[Distribution("manual", "https://x/home", note="站点拒绝机器人")],
            ),
            Entry(
                YB,
                "table",
                "2025/14-08",
                "2010-2024年规模以上工业增加值",
                edition_year=2025,
                path="第十四篇 工业",
                topics=topics("cnstat.industry", "cnstat.industry.value_added"),
                regions=[
                    RegionLink("CN-SD", "covers", None, "exact", "structure"),
                    RegionLink("CN-SD", "reports_at", "prefecture", "descendants", "inferred"),
                ],
                distributions=[
                    Distribution("direct_file", "https://x/2025/14-08.jpg", "jpg", "image")
                ],
            ),
            Entry(
                YB,
                "table",
                "2024/14-08",
                "2009-2023年规模以上工业增加值",
                edition_year=2024,
                path="第十四篇 工业",
                topics=topics("cnstat.industry", "cnstat.industry.value_added"),
                regions=[RegionLink("CN-SD", "covers", None, "exact", "structure")],
                distributions=[
                    Distribution("direct_file", "https://x/2024/14-08.xls", "xls", "tabular")
                ],
            ),
            Entry(
                YB,
                "table",
                "2025/2-1",
                "人口数及构成",
                edition_year=2025,
                path="第三篇 人口",
                topics=topics("cnstat.population"),
                regions=[RegionLink("CN-SD", "covers", None, "exact", "structure")],
            ),
        ]
    )
    return cat


@pytest.fixture
def cat(tmp_path):
    c = build(Catalog.open(tmp_path / "catalog.db"))
    yield c
    c.close()
