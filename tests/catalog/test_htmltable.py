"""网页统计表解析：真实页面样本（统计年鉴、普查、上海年鉴含框架页、UTF-16）+ 合成的边界情形。

核心验收是"不丢、不造"：
- 不造：输出的每个数值都能在页面文字里找到；
- 不丢：输出行数等于页面里 Excel 标注的数值单元格数（x:num）。
"""

import re
from html import unescape
from pathlib import Path

import pytest

from datafinder.catalog.adapters import AdapterError
from datafinder.catalog.files import decode_html
from datafinder.catalog.htmltable import (
    clean,
    frame_sources,
    has_table,
    parse_grid,
    parse_number,
    to_table,
)

FIX = Path(__file__).parents[1] / "fixtures" / "html_tables"


def page(name: str) -> str:
    return decode_html((FIX / name).read_bytes())


def page_text(html: str) -> str:
    return re.sub(r"[\s,\u3000\xa0]+", "", unescape(re.sub(r"<[^>]+>", " ", html)))


# ───────────── 真实页面 ─────────────


@pytest.mark.parametrize(
    "name,region",
    [
        ("cn_2005_1-13.htm", "全国"),
        ("sh_2010_11.2.htm", "上海"),
        ("sh_2010_1.11.htm", "上海"),
    ],
)
def test_every_excel_numeric_cell_becomes_exactly_one_row_and_no_value_is_invented(name, region):
    html = page(name)
    t = to_table(html, default_region=region)
    assert len(t.rows) == len(re.findall(r"x:num=", html))  # 不丢
    text = page_text(html)
    for row in t.rows:
        shown = f"{row[4]:.10g}"
        assert shown in text, f"{row[3]} 的值 {shown} 在页面上找不到"  # 不造
    assert t.meta["解析"]["数值单元格"] == len(t.rows) and t.meta["解析"]["可信度"] == "高"


def test_regional_table_labels_are_regions_with_units_from_header_rows():
    t = to_table(page("cn_2005_1-13.htm"))
    assert t.title == "各地区主要能源、黑色金属矿产基础储量（2004年）"
    first = t.rows[0]
    assert first[:3] == ["2004年", "2004YY", "全国"]  # 标题里的单一年份是期别
    assert first[3].endswith("· 石油") and first[4] == 249097.9 and first[5] == "万吨"
    assert {r[2] for r in t.rows} >= {"全国", "北京", "新疆"}
    units = {r[3].rsplit(" · ", 1)[1]: r[5] for r in t.rows}
    assert (
        units["天然气"] == "亿立方米" and units["铁矿"] == "矿石,亿吨"
    )  # 第二行表头是单位行；单位照原样写
    assert all("资料来源" not in r[3] and "注：" not in r[3] for r in t.rows)  # 脚注不当数据


def test_district_table_uses_unit_row_and_skips_footnote_cells_spanning_the_width():
    t = to_table(page("sh_2010_11.2.htm"), default_region="上海")
    total = {r[3].rsplit(" · ", 1)[1]: (r[4], r[5]) for r in t.rows if r[2] == "上海"}
    assert total["单位数"] == (17906.0, "个") and total["从业人员"] == (289.89, "万人")
    assert total["利润总额"] == (1431.97, "亿元")
    assert "浦东新区" in {r[2] for r in t.rows}  # 区县名当地区
    assert not any("①" in r[3] or "本表为" in r[3] for r in t.rows)
    assert "本表为" in t.meta["说明"]


def test_header_years_are_periods_and_children_inherit_the_parent_label():
    t = to_table(page("sh_2010_1.11.htm"), default_region="上海")
    assert {r[1] for r in t.rows} == {"2000YY", "2008YY", "2009YY"}
    labels = {r[3] for r in t.rows}
    assert "主要年份人大情况 · 全国人大代表人数（人）" in labels
    assert "主要年份人大情况 · 全国人大代表人数（人） · 女性" in labels  # "# 女性" 属于上一行


def test_census_table_with_nested_headers_and_reference_year_fallback():
    t = to_table(page("pop_2010_1-1.htm"), default_year="2010")
    assert t.title == "各地区户数、人口数和性别比"
    assert len(t.rows) == 32 * 16  # 31 省 + 全国，16 个数值列
    assert t.rows[0][:3] == ["2010年", "2010YY", "全国"]
    assert (
        t.rows[0][3] == "各地区户数、人口数和性别比 · 户数 · 合计" and t.rows[0][4] == 417722698.0
    )
    assert t.rows[0][5] == "户、人"  # "单位：户、人" 在表头区
    no_year = to_table(page("pop_2010_1-1.htm"))
    assert no_year.rows[0][0] == ""  # 页面没写年份又没给参考年：不猜


def test_numeric_row_labels_are_kept_meaningful_and_date_ranges_are_not_a_period():
    t = to_table(page("pop_2010_6-4b.htm"), default_year="2010")
    assert t.rows[0][0] == "2010年"  # 标题里是"2009.11.1-2010.10.31"区间，不取其中一个年份
    ages = {r[3].split(" · ")[1] for r in t.rows}
    assert {"总计", "0-4岁", "年龄 0", "年龄 99", "100岁及以上"} <= ages
    assert len(t.rows) == t.meta["解析"]["数值单元格"] == 1098


def test_table_mixing_year_rows_and_region_rows():
    t = to_table(page("cn_2012_13-23.htm"))
    by_year = [r for r in t.rows if r[0] == "1985年"]
    assert len(by_year) == 8 and by_year[0][2] == "全国" and by_year[0][4] == 16.27
    regions = {r[2] for r in t.rows if r[0] == ""}
    assert {"辽宁", "新疆"} <= regions  # 年份行是期别，地区行是地区，互不冒充
    assert t.rows[0][5] == "公斤"


def test_utf16_page_and_year_headers_that_are_categories_not_periods():
    html = decode_html((FIX / "econ_2004_1-11_utf16.htm").read_bytes())
    assert "开业" in html  # UTF-16 带 BOM 的页面能正确解码
    t = to_table(html, default_year="2004")
    assert len(t.rows) == 480 and {r[1] for r in t.rows} == {
        "2004YY"
    }  # 表头里的年份是"开业时间"分类
    cols = {r[3].split(" · ", 1)[1] for r in t.rows}
    assert any("1949" in c and "及以前" in c for c in cols) and "1950-1977" in cols


def test_frameset_pages_are_stacked_title_header_frame_over_data_frame():
    top, data = page("sh_2013_C1301A.htm"), page("sh_2013_C1301B.htm")
    frames = frame_sources(page("sh_2013_C1301.htm"), "https://t.example/tjnj/2013tjnj/C1301.htm")
    assert frames == [
        "https://t.example/tjnj/2013tjnj/C13/C1301A.htm",
        "https://t.example/tjnj/2013tjnj/C13/C1301B.htm",
    ]
    assert not has_table(page("sh_2013_C1301.htm")) and has_table(top) and has_table(data)
    t = to_table([top, data], default_region="上海")
    assert t.title == "工业总产值及指数（1978～2012）"
    first = t.rows[0]
    assert first[:3] == ["1978年", "1978YY", "上海"] and first[4] == 514.01 and first[5] == "亿元"
    assert first[3].endswith("工业总产值 · 合计")  # 单位从"（亿元）"里取出，名称保持干净
    assert len(t.rows) == 210 and 1073.84 in {r[4] for r in t.rows}  # "1 073.84" 的千分位空格


# ───────────── 合成的边界情形 ─────────────


def test_clean_and_parse_number():
    assert clean("地\xa0\xa0区") == "地区" and clean("GDP  total") == "GDP total"
    assert parse_number("1 073.84") == 1073.84 and parse_number("-3.5%") == -3.5
    assert parse_number("1,234") == 1234.0
    for na in ("—", "——", "…", "-", "", "注：1"):
        assert parse_number(na) is None


def html_of(rows: str) -> str:
    return f"<html><table>{rows}</table></html>"


def test_rowspan_and_colspan_are_expanded_and_na_markers_make_no_rows():
    h = html_of(
        "<tr><td colspan=4>表1-1 各地区产量（2020年）</td></tr>"
        "<tr><td rowspan=2>地区</td><td colspan=2>产量</td><td rowspan=2>面积</td></tr>"
        "<tr><td>小麦</td><td>玉米</td></tr>"
        "<tr><td>北京</td><td>1.5</td><td>—</td><td>3</td></tr>"
        "<tr><td>天津</td><td>——</td><td>2.5</td><td>4</td></tr>"
        "<tr><td>河北</td><td>5</td><td>6</td><td>7</td></tr>"
        "<tr><td colspan=4>注：数据为估算</td></tr>"
    )
    g = parse_grid(h)
    assert [c.text for c in g[1]] == ["地区", "产量", "产量", "面积"] and g[2][0].text == "地区"
    t = to_table(h)
    got = {(r[2], r[3].split(" · ", 1)[1]): r[4] for r in t.rows}
    assert got == {
        ("北京", "产量 · 小麦"): 1.5,
        ("北京", "面积"): 3.0,
        ("天津", "产量 · 玉米"): 2.5,
        ("天津", "面积"): 4.0,
        ("河北", "产量 · 小麦"): 5.0,
        ("河北", "产量 · 玉米"): 6.0,
        ("河北", "面积"): 7.0,
    }  # 缺省记号不产生行；"注"在数据区之后，不是数据
    assert t.rows[0][0] == "2020年" and t.meta["说明"] == "注：数据为估算"


def test_indented_children_inherit_parent_and_totals_map_to_the_default_region():
    h = html_of(
        "<tr><td colspan=2>表3-1 国民经济核算</td></tr>"
        "<tr><td>项目</td><td>数值（亿元）</td></tr>"
        "<tr><td>国内生产总值</td><td>100</td></tr>"
        "<tr><td>&nbsp;&nbsp;第一产业</td><td>10</td></tr>"
        "<tr><td>&nbsp;&nbsp;第二产业</td><td>40</td></tr>"
        "<tr><td>其中：工业</td><td>35</td></tr>"
        "<tr><td>固定资产投资</td><td>60</td></tr>"
    )
    t = to_table(h, default_region="河北")
    labels = [r[3] for r in t.rows]
    assert labels == [
        "国民经济核算 · 国内生产总值 · 数值",
        "国民经济核算 · 国内生产总值 · 第一产业 · 数值",
        "国民经济核算 · 国内生产总值 · 第二产业 · 数值",
        "国民经济核算 · 国内生产总值 · 第二产业 · 工业 · 数值",
        "国民经济核算 · 固定资产投资 · 数值",
    ]
    assert {r[5] for r in t.rows} == {"亿元"} and {r[2] for r in t.rows} == {"河北"}


def test_total_rows_in_a_region_table_belong_to_the_default_region():
    h = html_of(
        "<tr><td colspan=2>表2-1 各区人口（2020年）</td></tr><tr><td>地区</td><td>人口（万人）</td></tr>"
        "<tr><td>总计</td><td>100</td></tr><tr><td>北京</td><td>40</td></tr>"
        "<tr><td>天津</td><td>30</td></tr><tr><td>河北</td><td>30</td></tr>"
    )
    t = to_table(h, default_region="全国")
    assert [(r[2], r[4]) for r in t.rows] == [
        ("全国", 100.0),
        ("北京", 40.0),
        ("天津", 30.0),
        ("河北", 30.0),
    ]
    assert t.meta["解析"]["行标签含义"] == "地区"


def test_errors_are_explicit_for_pages_without_tables_or_data():
    with pytest.raises(AdapterError, match="没有找到表格"):
        to_table("<html><img src='a.jpg'></html>")
    with pytest.raises(AdapterError, match="没有在页面表格里找到数据行"):
        to_table(html_of("<tr><td>说明</td></tr><tr><td>这是一张纯文字表</td></tr>"))


def test_low_confidence_is_reported_not_hidden():
    h = html_of("<tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr>")
    t = to_table(h)
    assert t.meta["解析"]["可信度"] == "低" and t.meta["解析"]["提示"]
    assert len(t.rows) == 4  # 仍然一个数值一行，只是明说"没认出表头"


def test_frame_layout_ignores_stray_empty_framesets_and_nests_rows_and_cols():
    from datafinder.catalog.htmltable import compose_grid, frame_layout, layout_urls

    # 上海年鉴的真实样子：页面末尾有多余的空 <frameset></frameset>
    stray = page("sh_2013_C1301.htm")
    tree = frame_layout(stray, "https://t.example/a/C1301.htm")
    assert layout_urls(tree) == [
        "https://t.example/a/C13/C1301A.htm",
        "https://t.example/a/C13/C1301B.htm",
    ]
    nested = (
        '<frameset rows="50,*"><frame src="a.htm"><frameset rows="58,*"><frameset cols="295,*">'
        '<frame src="corner.htm"><frame src="head.htm"></frameset><frameset cols="295,*">'
        '<frame src="labels.htm"><frame src="data.htm"></frameset></frameset></frameset>'
    )
    tree = frame_layout(nested, "https://t.example/x/")
    assert tree[0] == "rows" and tree[1][1][0] == "rows"
    assert tree[1][1][1][0][0] == "cols"  # 左右排布嵌在上下排布里
    pages = {
        "https://t.example/x/a.htm": "<table><tr><td colspan=3>表1 测试表（2020年）</td></tr></table>",
        "https://t.example/x/corner.htm": "<table><tr><td>类别</td></tr></table>",
        "https://t.example/x/head.htm": "<table><tr><td>甲（万人）</td><td>乙（万人）</td></tr></table>",
        "https://t.example/x/labels.htm": "<table><tr><td>总计</td></tr><tr><td>北京</td></tr><tr><td>天津</td></tr><tr><td>河北</td></tr></table>",
        "https://t.example/x/data.htm": "<table><tr><td>10</td><td>20</td></tr><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr><tr><td>6</td><td>14</td></tr></table>",
    }

    def build(node):
        return (node[0], [build(k) if isinstance(k, tuple) else pages[k] for k in node[1]])

    layout = build(tree)
    assert compose_grid(layout) is not None
    t = to_table(layout, default_region="全国")
    got = {(r[2], r[3].split(" · ")[-1]): r[4] for r in t.rows}
    assert (
        got[("全国", "甲")] == 10.0 and got[("北京", "乙")] == 2.0 and got[("河北", "乙")] == 14.0
    )
    assert (
        len(t.rows) == 8 and t.rows[0][5] == "万人" and t.rows[0][0] == "2020年"
    )  # 角标、表头、行标签、数据各归其位


def test_stacked_year_blocks_keep_their_section_title_and_year_labels_without_monotonic_order():
    h = html_of(
        "<tr><td colspan=3>表2-8 第三产业不变价增加值</td></tr>"
        "<tr><td>单位：亿元</td></tr>"
        "<tr><td>年份</td><td>第三产业</td><td>其他</td></tr>"
        "<tr><td></td><td colspan=2>按1970年价格</td></tr>"
        "<tr><td>1978</td><td>846.0</td><td>245.4</td></tr>"
        "<tr><td>1979</td><td>912.4</td><td>270.1</td></tr>"
        "<tr><td></td><td colspan=2>按1980年价格</td></tr>"
        "<tr><td>1980</td><td>982.9</td><td>375.0</td></tr>"
        "<tr><td>1978</td><td>800.0</td><td>200.0</td></tr>"  # 年份回头（新的一组从头开始）
    )
    t = to_table(h)
    rows = [(r[1], r[3].split(" · ", 1)[1], r[4]) for r in t.rows]
    assert ("1978YY", "按1970年价格 · 第三产业", 846.0) in rows
    assert ("1980YY", "按1980年价格 · 其他", 375.0) in rows
    assert ("1978YY", "按1980年价格 · 第三产业", 800.0) in rows  # 同一年在两个段里不混
    assert len(rows) == 8 and {r[5] for r in t.rows} == {"亿元"}


GRID_HTML = """
<script>
  $(function () {
    var data = {"templetname":"国民经济和社会发展总量与速度","fixedRowsTop":2,
      "data":[["指标","","1978","2024"],["","","",""],
              ["年末常住人口","(万人)","5985","6670"],
              ["地区生产总值","(亿元)","249","90131"]],
      "mergeCells":[{"col":0,"row":0,"colspan":2,"rowspan":2},
                    {"col":2,"row":0,"colspan":1,"rowspan":2},
                    {"col":3,"row":0,"colspan":1,"rowspan":2}]};
    $('#x').datagrid(data);
  });
</script>
"""


def test_grid_html_restores_merged_cells_and_parses_to_tidy_table():
    """浙江年鉴的表是脚本注入的网格（页面里没有 <table>），按 mergeCells 还原后照样能解析。"""
    from datafinder.catalog.htmltable import grid_html, to_table

    html = grid_html(GRID_HTML)
    assert html is not None and html.count("rowspan") >= 3
    table = to_table(html, default_region="浙江", source_url="https://t.example/1-1.html")
    # 一个数值单元格 = 一行：1978/2024 两个年份 × 两个指标
    assert len(table.rows) == 4
    assert {r[0] for r in table.rows} == {"1978年", "2024年"}
    assert any("年末常住人口" in r[3] and r[4] == 6670.0 for r in table.rows)
    assert table.meta["解析"]["可信度"] in ("中", "高")  # 表名还原成标题行后不该判低


def test_grid_html_returns_none_for_plain_pages():
    from datafinder.catalog.htmltable import grid_html

    assert grid_html("<html><body><p>没有网格</p></body></html>") is None
