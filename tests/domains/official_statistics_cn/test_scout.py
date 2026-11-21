"""省级站点探测：栏目识别、版次抽取、目录形态判定。规则都对着一轮实测的站点写过。"""

from __future__ import annotations

from datafinder.domains.official_statistics_cn.scout import (
    classify,
    detect_format,
    find_editions,
    toc_urls_for,
)


def test_classify_channel_titles():
    assert classify("统计年鉴") == "年鉴"
    assert classify("统计公报") == "公报"
    assert classify("普查数据") == "普查"
    assert classify("统计数据查询") == "数据"
    assert classify("指标查询") == "指标"
    assert classify("统计制度") == "制度"
    assert classify("时政要闻") is None


def test_find_editions_uses_title_year_then_path_year():
    html = """
    <a href="/tjnj/nj2025/zk/indexch.htm">山东省统计年鉴-2025</a>
    <a href="/tjnj/nj2012/indexch.htm">山东省统计年鉴--2012</a>
    <a href="/art/2024/1/1/art_1_1.html">关于年鉴的说明</a>
    <a href="/tjnj/nj2001/2001.htm">山东省统计年鉴-2001</a>
    """
    eds = find_editions(html, "http://x.gov.cn/col/col6279/index.html")
    assert [e.year for e in eds] == [2025, 2012, 2001]  # 倒序
    assert eds[0].url == "http://x.gov.cn/tjnj/nj2025/zk/indexch.htm"


def test_detect_format_markers():
    assert detect_format('<li id="foldheader">综合</li>') == "zk"
    assert detect_format('<div class="chapterItem">一 综合</div>') == "fj"
    assert detect_format("var data={data:[],mergeCells:[]}") == "yb"
    assert detect_format('<a href="../book/1-1.htm">行政区划</a>') == "book"
    assert detect_format('<a href="/extra/col20/2012/0101.htm">区划</a>') == "heb"
    assert (
        detect_format("<a href='zk/html/0001.jpg'>区划</a><li id='foldheader'>综合</li>") == "heb"
    )
    assert detect_format("const contents='[{...}]'") == "json"
    assert detect_format('<u class="Outline">第一篇、综合</u>') == "ml"
    assert detect_format("<html><body>没有目录</body></html>") is None


def test_toc_urls_cover_directory_and_frame_entry():
    assert "http://x.cn/tjnj/nj2024/zk/left.htm" in toc_urls_for(
        "http://x.cn/tjnj/nj2024/zk/indexch.htm"
    )
    assert "http://x.cn/tjnj/nj2024/zk/left.htm" in toc_urls_for("http://x.cn/tjnj/nj2024/zk/")
    # 文章型版次（河北 2008–2012）本身也可能是目录页
    assert (
        toc_urls_for("http://x.cn/a/101702088122468.html")[0]
        == "http://x.cn/a/101702088122468.html"
    )
