"""Тест каталог-парсинга (ebay_library: srp/urls/normalize/page_state) — офлайн.

Чистые функции: build_search_url, normalize_condition, page_state-классификация
(порт под in-page fetch). parse_search_page — на реальных SRP-фикстурах
``tests/fixtures/srp_*.html`` (если есть; они gitignored — пропускаем при отсутствии).
fx (сеть) тут не гоняем. Модули скопированы из ``ebaylib`` (SPEC.md §12).

Запуск: ``PYTHONPATH=. python3 tests/test_catalog_parse.py``.
"""

from __future__ import annotations

import os

from ebay_library.html.normalize import normalize_condition
from ebay_library.html.page_state import Antibot, PageKind, detect_state_html
from ebay_library.html.srp import parse_search_page
from ebay_library.urls import build_search_url

_FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_build_search_url():
    u = build_search_url("805079", page=2, zip="19701", condition="new",
                         min_price=50, max_price=500)
    assert "_nkw=805079" in u and "_pgn=2" in u and "_ipg=240" in u
    assert "_dmd=1" in u                       # list-view (продавец в карточках)
    assert "_stpos=19701" in u                 # ZIP доставки
    assert "LH_ItemCondition=3" in u           # new
    assert "_udlo=50" in u and "_udhi=500" in u


def test_normalize_condition():
    assert normalize_condition("Brand New") == "new"
    assert normalize_condition("New with tags") == "new"
    assert normalize_condition("Open box") == "new"
    assert normalize_condition("Pre-Owned") == "other"
    assert normalize_condition("Used") == "other"
    assert normalize_condition("Bananas") is None


def test_page_state_classification():
    def st(url, title):
        return detect_state_html(url, f"<html><head><title>{title}</title></head></html>")
    srp = st("https://www.ebay.com/sch/i.html?_nkw=x", "x for sale | eBay")
    assert srp.kind is PageKind.SRP and srp.antibot is None
    pardon = st("https://www.ebay.com/splashui/challenge?a=1", "Pardon Our Interruption")
    assert pardon.kind is PageKind.PARDON and pardon.antibot is Antibot.PARDON
    denied = st("https://www.ebay.com/sch/i.html?_nkw=x", "Access Denied")
    assert denied.antibot is Antibot.ACCESS_DENIED
    err = st("https://www.ebay.com/sch/i.html?_nkw=x", "Error Page | eBay")
    assert err.antibot is Antibot.ERROR_PAGE


def _read(name):
    p = os.path.join(_FIX, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def test_parse_search_page_real():
    html = _read("srp_8M0142836_zip19701.html")
    if html is None:
        print("  (skip: нет фикстуры srp_8M0142836_zip19701.html)")
        return
    sp = parse_search_page(html)
    assert sp.results_count > 0, sp.results_count
    assert len(sp.items) > 0, "ожидались карточки"
    c = sp.items[0]
    assert len(c.item_id) == 12 and c.item_id.isdigit(), c.item_id
    assert c.title and c.price > 0 and c.currency_raw and c.seller, c


def test_parse_search_page_empty():
    html = _read("srp_empty_09651605.html")
    if html is None:
        print("  (skip: нет фикстуры srp_empty_09651605.html)")
        return
    sp = parse_search_page(html)
    assert sp.results_count == 0 and sp.items == [], sp


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
