from datafinder.yearbook import index as yb
from datafinder.yearbook.parse import TocEntry


def E(chapter, no, title, fmt="jpg"):
    return TocEntry(chapter, no, title, f"https://x/{no}.{fmt}", fmt, "table")


def fill(conn):
    yb.replace_edition(conn, "cn", 2024, status="ok", toc_url="u24", entries=[
        E("十四、工业", "14-1", "2014-2023年规模以上工业增加值"), E("一、综合", "1-1", "全国行政区划(2023年底)"),
        TocEntry("一、综合", "", "简要说明", "https://x/sm.htm", "html", "note")])
    yb.replace_edition(conn, "cn", 2025, status="ok", toc_url="u25", entries=[
        E("十四、工业", "14-1", "2015-2024年规模以上工业增加值"), E("一、综合", "1-1", "全国行政区划(2024年底)")])
    yb.replace_edition(conn, "CN-SD", 2025, status="ok", toc_url="s25", entries=[
        E("第十四篇 工业", "14-8", "2010-2024年规模以上工业增加值"), E("第七篇 能源", "7-1", "能源消费总量")])
    yb.replace_edition(conn, "CN-SD", 2016, status="missing", toc_url=None, message="404")


def test_search_merges_same_table_across_years_and_lists_all_editions():
    conn = yb.connect_index()
    fill(conn)
    hits = yb.search(conn, "工业增加值", series="cn")
    assert len(hits) == 1
    h = hits[0]
    assert h["years"] == [2025, 2024] and h["title"].startswith("2015-2024年")  # 标题取最新版次
    assert [e["year"] for e in h["editions"]] == [2025, 2024]


def test_search_prefers_named_region_and_includes_national():
    conn = yb.connect_index()
    fill(conn)
    hits = yb.search(conn, "山东 工业增加值")
    assert hits[0]["series"] == "CN-SD" and {h["series"] for h in hits} == {"CN-SD", "cn"}
    only_national = yb.search(conn, "工业增加值")  # 没点名地区：全国与各省都参与
    assert {h["series"] for h in only_national} == {"cn", "CN-SD"}


def test_search_filters_year_fmt_and_ignores_notes_and_stopwords():
    conn = yb.connect_index()
    fill(conn)
    assert [h["years"] for h in yb.search(conn, "工业增加值", series="cn", year=2024)] == [[2024]]
    assert yb.search(conn, "工业增加值", fmt="xls") == []
    assert yb.search(conn, "简要说明") == []  # 说明类条目不参与检索
    assert yb.search(conn, "统计年鉴 数据") == []  # 只剩无区分度的词：不返回垃圾


def test_coverage_stats_and_rebuild_norms():
    conn = yb.connect_index()
    fill(conn)
    cov = {c["series"]: c for c in yb.coverage(conn)}
    assert cov["cn"]["years_ok"] == [2024, 2025] and cov["cn"]["tables"] == 4
    assert cov["CN-SD"]["years_missing"] == [2016] and cov["CN-SH"]["crawlable"] is False
    assert yb.stats(conn)["tables"] == 6
    conn.execute("UPDATE ytable SET norm='x'")
    assert yb.rebuild_norms(conn) == 7  # 含说明类条目
    assert yb.search(conn, "工业增加值", series="cn")[0]["years"] == [2025, 2024]


def test_replace_edition_is_idempotent():
    conn = yb.connect_index()
    fill(conn)
    fill(conn)
    assert conn.execute("SELECT COUNT(*) FROM ytable WHERE series='cn' AND year=2025").fetchone()[0] == 2
