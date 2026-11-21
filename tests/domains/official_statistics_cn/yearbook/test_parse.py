from pathlib import Path

from datafinder.domains.official_statistics_cn.yearbook.parse import parse_toc

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
    assert (
        entries["15.1"].chapter == "第十五章 其他"
        and entries["15.1"].url == "https://example.gov.cn/sj/ndsj/x/C15-01.HTM"
    )  # ../ 相对路径正确解析
    assert entries["15.1"].fmt == "html"


def test_appendix_tables_with_prefixed_numbers_are_tables_not_notes():
    html = "<ul><li id='foldheader'>附录、国际主要社会经济指标</li><li><a href='h/a.jpg'>附录1-15 货币汇率(年平均价)</a></li><li><a href='h/b.jpg'>表2.1 户数</a></li></ul>"
    got = [(e.no, e.title, e.kind) for e in parse_toc(html, "https://x/")]
    assert got == [("附录1-15", "货币汇率(年平均价)", "table"), ("2.1", "户数", "table")]


# ── 其他目录形态（安徽 json / 吉林 ml / 浙江 yb / 江苏 fp）──

from datafinder.domains.official_statistics_cn.yearbook.parse import (  # noqa: E402
    parse_fp_chapters,
    parse_fp_toc,
    parse_json_toc,
    parse_ml_toc,
    parse_yb_toc,
)

AH_HTML = """
<script>
  const contents = '[{"index":-1,"label":"编辑说明","link":"编辑说明.html"},
  {"children":[{"label":"1-1 全省行政区划（2024年底）","link":"1-1.html"},
               {"label":"1-2 全省县以上行政区划（2024年底）","link":"1-2.html"}],
   "label":"一、综　　合"}]'
</script>
"""


def test_parse_json_toc_reads_embedded_array_even_without_closing_quote():
    """安徽的目录是内嵌 JSON；实测有页面缺结尾引号，所以按括号配平截取。"""
    entries = parse_json_toc(AH_HTML, "https://example.gov.cn/tjnj/2025/")
    tables = [e for e in entries if e.kind == "table"]
    assert [e.no for e in tables] == ["1-1", "1-2"]
    assert tables[0].chapter.replace("\u3000", "") == "一、综合"
    assert tables[0].url == "https://example.gov.cn/tjnj/2025/1-1.html"
    assert [e.title for e in entries if e.kind == "note"] == ["编辑说明"]


JL_HTML = """
<table><tbody>
<tr><td><a href="./fp1.html" target="right"><span class="Outline">第1章 综合</span></a></td></tr>
<tr id="Content"><td>
  <a href="../html/1-1.html" target="right"><dd>1-1 行政区划(2024年底)</dd></a>
  <a href="../html/1-2.html" target="right"><dd>1-2 自然资源状况</dd></a>
</td></tr>
<tr><td><a href="./fp2.html" target="right"><span class="Outline">第2章 人口</span></a></td></tr>
<tr><td><a href="../html/2-1.html" target="right"><dd>2-1 人口数</dd></a></td></tr>
</tbody></table>
"""


def test_parse_ml_toc_uses_chapter_pages_to_group_tables():
    entries = [
        e
        for e in parse_ml_toc(JL_HTML, "https://example.gov.cn/2025/ml/njmlc.htm")
        if e.kind == "table"
    ]
    assert [(e.chapter, e.no) for e in entries] == [
        ("第1章 综合", "1-1"),
        ("第1章 综合", "1-2"),
        ("第2章 人口", "2-1"),
    ]
    assert entries[0].url == "https://example.gov.cn/2025/html/1-1.html"


ZJ_HTML = """
<div class="yb-tree"><ul class="yb-menu">
  <li class="yb-submenu"><div class="yb-submenu-title"><span>一、综合</span></div>
    <ul class="yb-menu">
      <li class="yb-menu-item"><a href="javascript:;" main="./cn/html/1-1 国民经济和社会发展总量与速度.html">1-1 国民经济和社会发展总量与速度</a></li>
      <li class="yb-menu-item"><a href="javascript:;" main="./cn/html/1-2 结构指标.html">1-2 结构指标</a></li>
    </ul>
  </li>
</ul></div>
"""


def test_parse_yb_toc_reads_main_attribute_and_chapter_titles():
    entries = [
        e
        for e in parse_yb_toc(ZJ_HTML, "https://example.cn/2025浙江统计年鉴/indexcn.html")
        if e.kind == "table"
    ]
    assert [e.no for e in entries] == ["1-1", "1-2"]
    assert entries[0].chapter == "一、综合"
    assert entries[0].url.endswith("/cn/html/1-1 国民经济和社会发展总量与速度.html")


JS_INDEX = """
<td onclick="location.href='nj01.htm'">综合</td>
<a href="indexc.htm">封面</a><a href="nj01.htm">综合</a><a href="nj02.htm">国民经济核算</a>
"""

JS_CHAPTER = """
<title>国民经济核算</title>
<a href="indexc.htm">封面</a><a href="nj01.htm">综合</a>
<a href="nj02/nj0200.htm">简要说明</a>
<a href="nj02/nj0201.htm">2-1 主要年份地区生产总值</a>
<a href="nj02/nj0202.htm">2-2 主要年份地区生产总值构成</a>
"""


def test_parse_fp_chapters_and_tables():
    chapters = parse_fp_chapters(JS_INDEX, "http://example.gov.cn/2025/indexc.htm")
    assert chapters == [
        "http://example.gov.cn/2025/nj01.htm",
        "http://example.gov.cn/2025/nj02.htm",
    ]
    entries = [e for e in parse_fp_toc(JS_CHAPTER, chapters[1]) if e.kind == "table"]
    assert [e.no for e in entries] == ["2-1", "2-2"]
    assert entries[0].chapter == "国民经济核算"
    assert entries[0].url == "http://example.gov.cn/2025/nj02/nj0201.htm"


FJ_TREE = """
<ul class="chapters">
  <div class="articleItem"><a href="./preface-cn.htm" class="article">前言</a></div>
  <div class="chapterItem" onclick="clickChapter(this);"><img src="./resources/chapter.gif" />&nbsp;一 综合</div>
  <ul class="reports">
    <li><a href="./cn/html/01a.htm">简要说明</a></li>
    <li><a href="./cn/html/0101.htm">1-1 全省行政区划（2021年底）</a></li>
    <li><a href="./cn-en/html/0102.htm">1-2 国民经济和社会发展总量和速度指标</a></li>
  </ul>
  <div class="chapterItem"><img src="./resources/chapter.gif" />&nbsp;二 国民经济核算</div>
  <ul class="reports"><li><a href="./cn-en/html/0201.htm">2-1 地区生产总值</a></li></ul>
</ul>
"""


def test_parse_fj_toc_reads_disc_tree_and_delegates_zk_pages():
    """福建 2011–2022 是光盘版树形目录；2023 起是 zk 目录页，同一个解析器要都能处理。"""
    from datafinder.domains.official_statistics_cn.yearbook.parse import parse_fj_toc

    entries = [
        e for e in parse_fj_toc(FJ_TREE, "https://x.cn/dz2022/contents-cn.htm") if e.kind == "table"
    ]
    assert [(e.chapter, e.no) for e in entries] == [
        ("一 综合", "1-1"),
        ("一 综合", "1-2"),
        ("二 国民经济核算", "2-1"),
    ]
    assert entries[0].url == "https://x.cn/dz2022/cn/html/0101.htm"
    # 没有 chapterItem 的页面交给 zk 解析器
    zk = [e for e in parse_fj_toc(HTML, BASE) if e.kind == "table"]
    assert [e.no for e in zk] == ["1-1", "1-2", "1-3", "14-1", "14-2", "15.1"]


HEB_OLDTOC = """
<ul>
  <li><a href="/extra/col20/2012/note.htm">统计资料使用说明</a></li>
  <li id="foldheader"> 综合 </li>
  <ul id="foldinglist">
    <li><a href="/extra/col20/2012/0101.htm">行政区划基本情况(2011年底)</a></li>
    <li><a href="/extra/col20/2012/0102.htm">自然状况和资源</a></li>
  </ul>
  <li id="foldheader">人口</li>
  <ul id="foldinglist"><li><a href="/extra/col20/2012/0201.htm">人口主要指标</a></li></ul>
</ul>
"""

HEB_IMG = """
<li id="foldheader">综合</li>
<ul id="foldinglist">
  <li><a href="zk/html/0001.jpg">行政区划基本情况(2015年底)</a></li>
  <li><a href="zk/html/0002.jpg">自然状况和资源</a></li>
</ul>
"""


def test_parse_heb_toc_derives_numbers_from_filenames_and_keeps_image_tables():
    """河北：2008–2012 表号在文件名里（0101→1-1）；2013–2019 是图片版，没有表号。"""
    from datafinder.domains.official_statistics_cn.yearbook.parse import parse_heb_toc

    old = [
        e
        for e in parse_heb_toc(HEB_OLDTOC, "https://x.cn/tjnj/101702088122468.html")
        if e.kind == "table"
    ]
    assert [(e.chapter, e.no, e.fmt) for e in old] == [
        ("综合", "1-1", "html"),
        ("综合", "1-2", "html"),
        ("人口", "2-1", "html"),
    ]
    assert old[0].url == "https://x.cn/extra/col20/2012/0101.htm"

    img = [
        e for e in parse_heb_toc(HEB_IMG, "https://x.cn/res/nj2016/left.htm") if e.kind == "table"
    ]
    assert [(e.chapter, e.no, e.fmt) for e in img] == [("综合", "", "jpg"), ("综合", "", "jpg")]
    assert img[0].url == "https://x.cn/res/nj2016/zk/html/0001.jpg"

    # 2020 起是标准 zk 目录页：交给 parse_toc，表号来自标题
    zk = [e for e in parse_heb_toc(HTML, BASE) if e.kind == "table"]
    assert [e.no for e in zk] == ["1-1", "1-2", "1-3", "14-1", "14-2", "15.1"]
