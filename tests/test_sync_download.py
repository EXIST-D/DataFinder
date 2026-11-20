from datafinder.portals.sync import mirror_urls


def test_mirrors_are_tried_in_order_and_pinned_to_ref():
    urls = mirror_urls("v1.27.0")
    assert urls[0].startswith("https://raw.githubusercontent.com/")
    assert urls[1].startswith("https://cdn.jsdelivr.net/")
    assert all("v1.27.0" in u for u in urls)


def test_registry_url_override_goes_first(monkeypatch):
    monkeypatch.setenv("DATAFINDER_REGISTRY_URL", "https://example.org/full.parquet")
    assert mirror_urls("main")[0] == "https://example.org/full.parquet"
