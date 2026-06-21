"""Тест JSON-first парсера товара (ebay_library.html.item) — Слой 1, офлайн.

Без сети: основа — реальная JSON-модель ``examples/item_modules_sample.json``
(товар 277574984378), обёрнутая в HTML как на странице (модель + ``sellerUserName``
вне неё). Варианты собираем мутацией копии модели. Сверено живьём (этап 1, SPEC.md §13).

Запуск: ``PYTHONPATH=. python3 tests/test_item_json.py``.
"""

from __future__ import annotations

import copy
import json
import os

from ebay_library.errors import ParseError
from ebay_library.html.item import parse_item_page

_SAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "item_modules_sample.json")


def _modules() -> dict:
    with open(_SAMPLE, encoding="utf-8") as f:
        return json.load(f)


def _wrap(modules: dict, *, seller: str | None = "fltoolbox") -> str:
    """HTML как на странице товара: встроенная модель + sellerUserName вне неё."""
    seller_js = f'var s={{"sellerUserName":"{seller}"}};' if seller is not None else ""
    return ("<html><head><title>x</title></head><body><script>"
            'window.__data={"modules":' + json.dumps(modules) + "};"
            + seller_js + "</script></body></html>")


def test_parse_known_item():
    html = _wrap(_modules())
    it = parse_item_page(html, description_html="<html><body>Hello desc</body></html>")
    assert it.item_number == "277574984378", it.item_number
    assert it.title == "A1 Mercury Quicksilver 8M0082537 Throttle/Shift Cable 14ft OEM New Boat Parts", it.title
    assert it.condition == "new", it.condition
    assert it.seller == "fltoolbox", it.seller
    assert it.location == "Crystal River, FL, United States", it.location
    # Mode 1: цену и доставку не парсим (SPEC.md §4.4)
    assert it.price_usd is None and it.shipping_cost is None
    # boilerplate-строку "Condition" в specifics исключаем (решение a)
    assert "Condition" not in it.specifics, it.specifics
    assert it.specifics.get("Brand") == "Mercury", it.specifics
    assert len(it.specifics) == 3, it.specifics
    assert it.image_urls and all(u.endswith("s-l1600.jpg") for u in it.image_urls), it.image_urls
    assert it.description == "Hello desc", it.description


def test_description_optional():
    """Без description_html → description = "" (валидно, SPEC.md §4.5)."""
    it = parse_item_page(_wrap(_modules()))
    assert it.description == ""


def test_condition_used_maps_other():
    m = copy.deepcopy(_modules())
    m["JSONLD"]["product"]["offers"]["itemCondition"] = "https://schema.org/UsedCondition"
    assert parse_item_page(_wrap(m)).condition == "other"


def test_condition_absent_is_none():
    m = copy.deepcopy(_modules())
    m["JSONLD"]["product"]["offers"].pop("itemCondition", None)
    assert parse_item_page(_wrap(m)).condition is None


def test_no_photos_empty_list():
    """Нет mediaList → image_urls == [] (товар реально без фото, SPEC.md §4.5)."""
    m = copy.deepcopy(_modules())
    m["PICTURE"].pop("mediaList", None)
    assert parse_item_page(_wrap(m)).image_urls == []


def test_missing_modules_raises():
    try:
        parse_item_page("<html><body>no modules here</body></html>")
    except ParseError as e:
        assert e.field == "modules"
    else:
        raise AssertionError("ParseError(modules) ожидался")


def test_missing_seller_raises():
    """seller обязателен (SPEC.md §7.4): нет sellerUserName → ParseError."""
    try:
        parse_item_page(_wrap(_modules(), seller=None))
    except ParseError as e:
        assert e.field == "seller"
    else:
        raise AssertionError("ParseError(seller) ожидался")


def test_missing_title_raises():
    m = copy.deepcopy(_modules())
    m["JSONLD"]["product"]["name"] = ""
    try:
        parse_item_page(_wrap(m))
    except ParseError as e:
        assert e.field == "title"
    else:
        raise AssertionError("ParseError(title) ожидался")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
