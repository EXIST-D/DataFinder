from datafinder.query.tokenize import has_cjk, token_text, tokenize


def test_cjk_bigrams_cover_two_char_words():
    # FTS5 trigram 对 2 字词无结果；我们的 bigram 必须能表达 2 字词
    assert tokenize("人口") == ["人口"]
    assert tokenize("人口普查") == ["人口", "口普", "普查"]


def test_single_cjk_char_is_kept():
    assert tokenize("的") == ["的"]


def test_latin_words_lowercase_and_light_stemming():
    assert tokenize("Statistics Offices") == ["statistic", "office"]
    assert tokenize("Air-Quality") == ["air", "quality"]


def test_fullwidth_is_normalized():
    assert tokenize("ＧＤＰ２０２０") == ["gdp2020"]


def test_mixed_text():
    assert token_text("中国GDP数据") == "中国 gdp 数据"


def test_phrase_is_substring_of_document_tokens():
    doc = f" {token_text('国家统计局人口普查数据')} "
    query = f" {token_text('人口普查')} "
    assert query in doc


def test_has_cjk():
    assert has_cjk("人口 census")
    assert not has_cjk("population census")
