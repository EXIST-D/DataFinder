import pytest

from datafinder.service import ServiceError, load_table
from datafinder.tables import COLUMNS, Table, describe, describe_markdown, save, to_number


def tidy():
    rows = []
    # 两个地区 × 三年，一个指标
    for code, name, bj, sh in (("2021YY", "2021年", 100.0, 200.0), ("2022YY", "2022年", 110.0, 190.0), ("2023YY", "2023年", 121.0, 180.0)):
        rows.append([name, code, "北京", "工业增加值", bj, "亿元"])
        rows.append([name, code, "上海", "工业增加值", sh, "亿元"])
    return Table("测试表", rows, meta={"来源": "测试"})


def test_to_number_handles_strings_and_placeholders():
    assert to_number("1,234.5") == 1234.5
    assert to_number(3) == 3.0
    assert to_number("—") == "—"
    assert to_number(True) == "True"  # 布尔不当数值


def test_describe_series_and_growth():
    d = describe(tidy())
    bj = next(s for s in d["series"] if s["region"] == "北京")
    assert bj["n"] == 3 and bj["first"] == 100.0 and bj["last"] == 121.0
    assert bj["mean"] == pytest.approx(110.3333, rel=1e-3)
    assert bj["median"] == 110.0
    assert bj["total_growth"] == pytest.approx(0.21)
    assert bj["cagr"] == pytest.approx(0.1)  # 100 → 121 两年，年均 10%
    sh = next(s for s in d["series"] if s["region"] == "上海")
    assert sh["cagr"] < 0


def test_describe_cross_section_ranks_latest_period():
    d = describe(tidy())
    (c,) = d["cross_section"]
    assert c["period"] == "2023年"
    assert [r["region"] for r in c["ranking"]] == ["上海", "北京"]
    assert c["ranking"][0]["rank"] == 1 and c["ranking"][0]["value"] == 180.0


def test_describe_skips_non_numeric_and_never_invents_growth_for_negative():
    t = tidy()
    t.rows.append(["2024年", "2024YY", "北京", "工业增加值", "—", "亿元"])
    t.rows.append(["2021年", "2021YY", "天津", "亏损额", -5.0, "亿元"])
    t.rows.append(["2023年", "2023YY", "天津", "亏损额", 3.0, "亿元"])
    d = describe(t)
    assert d["skipped_non_numeric"] == 1
    tj = next(s for s in d["series"] if s["region"] == "天津")
    assert "cagr" not in tj and "total_growth" not in tj


def test_describe_requires_tidy_columns():
    with pytest.raises(ValueError):
        describe(Table("x", [[1, 2]], columns=["a", "b"]))


def test_describe_markdown_has_table_and_caveat():
    md = describe_markdown(describe(tidy()))
    assert "| 指标 | 地区 |" in md and "北京" in md and "口径" in md


def test_save_csv_roundtrip_has_bom_for_excel(tmp_path):
    path = save(tidy(), tmp_path / "a.csv")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    t = load_table(path, anywhere=True)
    assert t.columns[:5] == COLUMNS[:5] and len(t.rows) == 6 and t.rows[0][4] == 100.0


def test_save_xlsx_has_data_and_note_sheets(tmp_path):
    from openpyxl import load_workbook

    path = save(tidy(), tmp_path / "a.xlsx")
    wb = load_workbook(path)
    assert wb.sheetnames == ["数据", "说明"]
    assert wb["数据"].max_row == 7 and wb["说明"]["B1"].value == "测试表"
    assert len(load_table(path, anywhere=True).rows) == 6


def test_save_default_location_is_exports_dir_and_rejects_unknown_format(tmp_path):
    p = save(tidy(), fmt="csv")
    assert p.parent.name == "exports" and p.exists()
    with pytest.raises(ValueError):
        save(tidy(), tmp_path / "a.pdf")


def test_load_table_confined_to_exports_dir(tmp_path):
    outside = tmp_path / "x.csv"
    outside.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ServiceError, match="导出目录"):
        load_table(outside)
    inside = save(tidy())
    assert len(load_table(inside.name).rows) == 6  # 相对名解析到导出目录
    with pytest.raises(ServiceError, match="导出目录"):
        load_table("../../../etc/hosts")
