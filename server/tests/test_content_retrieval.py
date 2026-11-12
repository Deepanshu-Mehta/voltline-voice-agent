import pytest

from app.content import load_pages
from app.retrieval import chunk_page, tokenize


def test_catalog_is_consistent(products):
    assert 12 <= len(products) <= 15
    assert len({p.category for p in products}) == 4
    assert len({p.sku for p in products}) == len(products)
    for p in products:
        assert p.price > 0 and p.warranty_months in (12, 24)
        if p.stock == 0:
            assert p.restock_date, f"{p.sku} is out of stock with no restock date"


def test_chunks_stay_within_size(settings):
    for page in load_pages(settings.content_dir):
        for chunk in chunk_page(page):
            assert len(chunk.text.split()) <= 330, chunk.id


def test_model_numbers_tokenize_every_way():
    for text in ("VL-A300", "vl a300", "VLA300"):
        assert "a300" in tokenize(text) or "vla300" in tokenize(text)


@pytest.mark.parametrize("query, expected_url", [
    ("How much are the Pulse Buds Pro?", "/products/vl-a300-pulse-buds-pro"),
    ("Can I return earbuds I opened?", "/returns-warranty"),
    ("Does the SafeView camera support 5 GHz wifi", "/docs/safeview-camera-setup"),
    ("Will the thermostat work with baseboard heaters", "/products/vl-t700-thermosense-thermostat"),
    ("how much is next day delivery", "/pricing"),
    ("is the VL-D900 able to run two monitors", "/products/vl-d900-dockline-dock"),
])
def test_retrieval_finds_the_source_page(retriever, query, expected_url):
    urls = [c.url for c, _ in retriever.search(query, top_k=4)]
    assert expected_url in urls, urls
