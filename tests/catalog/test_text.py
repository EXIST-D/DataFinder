from datafinder.catalog.text import norm_title


def test_norm_title_aligns_editions_across_years():
    a = norm_title("2010-2024年规模以上工业增加值")
    b = norm_title("2009-2023年规模以上工业增加值")
    assert a == b == "规模以上工业增加值"
    assert norm_title("全国行政区划(2024年底)") == norm_title("全国行政区划(2023年底)")
    assert norm_title("按行业分 规模以上 企业，主要指标（2024年）") == "按行业分规模以上企业主要指标"
