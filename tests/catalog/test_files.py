from datafinder.catalog.files import decode_html, distribution_for, fmt_of


def test_fmt_of_and_decode():
    assert fmt_of("https://a/b/C0101.JPG?x=1") == "jpg"
    assert fmt_of("https://a/b/C0101.HTM") == "html"
    assert fmt_of("https://a/b/") == "other"
    assert decode_html("中国统计年鉴".encode("gb18030")) == "中国统计年鉴"
    assert decode_html("中国统计年鉴".encode()) == "中国统计年鉴"


def test_distribution_for_unknown_format_is_a_page_link():
    assert distribution_for("https://a/b.xlsx").usability == "tabular"
    odd = distribution_for("https://a/viewer?id=1")
    assert odd.access_method == "page" and odd.delivery == "link"
