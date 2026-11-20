from pathlib import Path

from datafinder.domains.official_statistics_cn.yearbook.parse import (
    decode,
    fmt_of,
    parse_toc,
)

HTML = (Path(__file__).parents[3] / "fixtures" / "yearbook_toc.htm").read_text(encoding="utf-8")
BASE = "https://example.gov.cn/sj/ndsj/2025/left.htm"


def test_parse_toc_chapters_numbers_and_absolute_urls():
    entries = parse_toc(HTML, BASE)
    tables = [e for e in entries if e.kind == "table"]
    assert [e.no for e in tables] == ["1-1", "1-2", "1-3", "14-1", "14-2", "15.1"]
    first = tables[0]
    assert first.chapter == "一、综 合" and first.title == "全国行政区划(2024年底)"
    assert first.url == "https://example.gov.cn/sj/ndsj/2025/html/C01-01.jpg" and first.fmt == "jpg"


def test_parse_toc_ignores_front_matter_and_script_links_but_keeps_notes():
    entries = parse_toc(HTML, BASE)
    titles = [e.title for e in entries]
    assert "版权声明" not in titles and "编委会" not in titles  # 第一章之前的链接不算
    assert "折叠" not in titles  # javascript: 链接
    notes = [e for e in entries if e.kind == "note"]
    assert {"简要说明", "主要统计指标解释", "English"} <= {e.title for e in notes}


def test_parse_toc_handles_trailing_space_in_href_and_plain_li_chapter():
    entries = {e.no: e for e in parse_toc(HTML, BASE) if e.kind == "table"}
    assert entries["1-3"].url.endswith("/html/Z0103C.xls") and entries["1-3"].fmt == "xls"
    assert entries["15.1"].chapter == "第十五章 其他" and entries["15.1"].url == "https://example.gov.cn/sj/ndsj/x/C15-01.HTM"  # ../ 相对路径正确解析
    assert entries["15.1"].fmt == "html"


def test_fmt_of_and_decode():
    assert fmt_of("https://a/b/C0101.JPG?x=1") == "jpg"
    assert fmt_of("https://a/b/C0101.HTM") == "html"
    assert fmt_of("https://a/b/") == "other"
    assert decode("中国统计年鉴".encode("gb18030")) == "中国统计年鉴"
    assert decode("中国统计年鉴".encode()) == "中国统计年鉴"
