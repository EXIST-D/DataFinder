"""栏目列表页解析：链接与日期、条目识别、翻页、跳转页、周期推断。"""

from datafinder.catalog.listing import (
    extract_links,
    infer_period,
    item_links,
    looks_like_article,
    page_url,
    redirect_target,
    site_of,
    total_pages,
)

BASE = "https://www.example.gov.cn/sj/zxfb/"

# 列表项把同一链接写三遍（适配屏宽），日期只跟在最后一个后面；页脚有一个同样"像文章"的链接
LIST = """
<div class="list-content"><ul>
<li><a class="a" href="./202611/t20261116_1.html">2026年10月份国民经济运行情况</a>
    <a class="b" href="./202611/t20261116_1.html">2026年10月份国民经济运行情况</a>
    <span> 2026-11-16 </span></li>
<li><a href="./202610/t20261015_2.html">2026年三季度居民收入和消费支出情况</a><span>2026-10-15</span></li>
<li><a href="./202609/t20260915_3.html">2026年8月份规模以上工业增加值增长5%</a><span>2026年9月15日</span></li>
<li><a href="./202610/t20261015_9.html">下一页</a></li>
</ul></div>
<div class="footer"><a href="/wzgl/202302/t20230217_1912857.html">服务条款与隐私声明</a>
<a href="javascript:;">无障碍</a><a href="#top">返回顶部</a></div>
<script>createPageHTML(5, 0, "index", "html");</script>
"""


def test_extract_links_merges_duplicates_and_attaches_trailing_dates():
    links = {x.href: x for x in extract_links(LIST, BASE)}
    first = links["https://www.example.gov.cn/sj/zxfb/202611/t20261116_1.html"]
    assert first.date == "2026-11-16"  # 重复链接合并后保留日期
    assert links["https://www.example.gov.cn/sj/zxfb/202609/t20260915_3.html"].date == "2026-09-15"
    assert not any(x.startswith("javascript") or x.endswith("#top") for x in links)


def test_item_links_prefer_dated_entries_and_drop_footer_and_pager_text():
    items = item_links(extract_links(LIST, BASE), BASE)
    assert [x.date for x in items] == [
        "2026-11-16",
        "2026-10-15",
        "2026-09-15",
    ]  # 页脚、翻页文字都不算


def test_item_links_follow_custom_regex_and_stay_on_the_same_site():
    html = '<a href="/a/b/report-1.pdf">年度报告一</a><a href="https://other.org/a/report-2.pdf">年度报告二</a>'
    got = item_links(extract_links(html, BASE), BASE, href=r"\.pdf$")
    assert [x.text for x in got] == ["年度报告一"]


def test_undated_lists_need_a_cluster_otherwise_not_a_list():
    scattered = "".join(
        f'<a href="/s{i}/202601/t2026010{i}_{i}.html">导航推荐位第{i}条</a>' for i in range(5)
    )
    assert item_links(extract_links(scattered, BASE), BASE) == []  # 分散在各处：导航，不是列表
    clustered = "".join(
        f'<a href="/jyb/202601/t2026010{i}_{i}.html">教育统计公告第{i}号</a>' for i in range(5)
    )
    assert len(item_links(extract_links(clustered, BASE), BASE)) == 5


def test_script_wrapped_lists_are_still_found():
    html = "<script type='text/xml'><datastore><![CDATA[<li><a href='/art/2026/9/22/art_1_2.html'>1—8月全省经济运行简况</a><span>2026-09-22</span></li>]]></datastore></script>"
    links = extract_links(html, "http://tj.example.gov.cn/col/col1/index.html")
    assert links[0].text == "1—8月全省经济运行简况" and links[0].date == "2026-09-22"


def test_trs_pagination():
    assert total_pages(LIST) == 5 and total_pages("<html></html>") == 1
    assert page_url(BASE, 2, LIST) == BASE + "index_1.html"
    assert page_url(BASE + "index.html", 3, LIST) == BASE + "index_2.html"


def test_redirect_pages():
    js = '<script language="javascript"> window.location = "./ndtjgb/"; </script>'
    assert (
        redirect_target(js, "https://x.gov.cn/tjsj/tjgb/") == "https://x.gov.cn/tjsj/tjgb/ndtjgb/"
    )
    meta = '<meta http-equiv="refresh" content="0; url=/new/list.html">'
    assert redirect_target(meta, "https://x.gov.cn/old/") == "https://x.gov.cn/new/list.html"
    assert redirect_target("<p>正文</p>" * 1000, "https://x.gov.cn/") is None  # 长页面不是跳转页


def test_site_and_article_helpers():
    assert site_of("https://data.tj.nx.gov.cn/a") == site_of("https://tj.nx.gov.cn/b")
    assert site_of("https://www.stats.gov.cn/") != site_of("https://www.moe.gov.cn/")
    assert looks_like_article("http://x.gov.cn/art/2026/9/22/art_1_2.html")
    assert not looks_like_article("http://x.gov.cn/col/col85666/index.html")


def test_infer_period_is_conservative():
    assert infer_period("2026年10月份国民经济运行情况", "2026-11-16") == (2026, "monthly", 2026)
    assert infer_period("2026年1—10月份全国固定资产投资", None) == (2026, "monthly", None)
    assert infer_period("2026年三季度居民收入", "2026-10-15") == (2026, "quarterly", 2026)
    assert infer_period("2025年上半年房地产市场基本情况", None)[1] == "quarterly"
    assert infer_period("2025年", "2026-02-28") == (2025, "annual", 2026)
    assert infer_period("2024年国民经济和社会发展统计公报", None)[1] == "annual"
    assert infer_period("国家统计局解读数据", None) == (None, None, None)
