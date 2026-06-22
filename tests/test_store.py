"""Тест store-клиента (ebay_library.store) — payload-форма, офлайн (без БД).

Главный риск: ``apply_item`` в Mode 1 НЕ должен слать ``price_usd``/``shipping_cost``
(каталог ими владеет, SPEC.md §2.3/§4.4). Серверные функции и владение проверены
живьём в ROLLBACK на этапе 5. Здесь — чистая проверка сериализации.

Запуск: ``PYTHONPATH=. python3 tests/test_store.py``.
"""

from __future__ import annotations

from ebay_library.models import Catalog, CatalogItem, ItemPage
from ebay_library.store import _catalog_payload, _item_payload


def _item() -> ItemPage:
    # price_usd/shipping_cost заполнены НАРОЧНО — payload их всё равно не шлёт (Mode 1)
    return ItemPage(
        item_number="277574984378", title="T", condition="new",
        price_usd=47.5, shipping_cost=5.0, seller="fltoolbox", location="FL, US",
        specifics={"Brand": "Mercury"}, image_urls=["u1", "u2"],
        description="d", last_updated=None,
    )


def test_item_payload_omits_price_and_shipping():
    p = _item_payload(_item())
    assert "price_usd" not in p, p
    assert "shipping_cost" not in p, p


def test_item_payload_has_required_keys():
    p = _item_payload(_item())
    assert set(p) == {"item_number", "title", "condition", "seller", "location",
                      "description", "last_updated", "specifics", "image_urls"}, set(p)
    assert p["item_number"] == "277574984378"
    assert p["specifics"] == {"Brand": "Mercury"}
    assert p["image_urls"] == ["u1", "u2"]


def test_catalog_payload_marks_usd_and_keeps_price_shipping():
    cat = Catalog(
        query="805079", results_count=1, pages_fetched=1, has_fewer_words_sep=False,
        items=[CatalogItem(item_id="123456789012", title="C", condition="new",
                           price=10.0, shipping_cost=0.0, seller="s", location="L",
                           image_url="img")],
    )
    p = _catalog_payload(cat)
    assert p["results_count"] == 1
    it = p["items"][0]
    assert it["currency"] == "USD"          # каталог пишет USD (после fx)
    assert it["price"] == 10.0              # каталог владеет ценой
    assert it["shipping_cost"] == 0.0       # и доставкой
    assert it["item_id"] == "123456789012"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
