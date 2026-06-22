"""Тест каталог-сессии (ebay_library.browser.catalog_session.fetch_catalog) — офлайн.

Браузерную страницу подменяем FakePage (``evaluate`` отдаёт канон-HTML из фикстур),
``convert_cards`` мокаем (без fx-сети). Покрываем: парс одной страницы, пустая выдача,
стоп по fewer-words, классификация Pardon → ``PardonError``. Многостраничную пагинацию
(continue) проверяем ЖИВЬЁМ на сервере (нет фикстуры с полными 240 карточками).

Запуск: ``PYTHONPATH=. python3 tests/test_catalog_session.py``.
"""

from __future__ import annotations

import asyncio
import os

import ebay_library.browser.catalog_session as CS
from ebay_library.errors import PardonError
from ebay_library.models import CatalogItem

_FIX = os.path.join(os.path.dirname(__file__), "fixtures")
_PARDON = "<html><head><title>Pardon Our Interruption</title></head><body/></html>"


def _read(name):
    p = os.path.join(_FIX, name)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


async def _fake_convert(cards, **kw):
    return [CatalogItem(item_id=c.item_id, title=c.title, condition=c.condition,
                        price=c.price, shipping_cost=c.shipping_cost, seller=c.seller,
                        location=c.location, image_url=c.image_url) for c in cards]


class FakePage:
    """Мок Playwright-страницы: evaluate(url) → {url,status,body} по responder."""
    def __init__(self, responder):
        self.responder = responder
        self.calls = []
        self.url = "https://www.ebay.com/"
    async def goto(self, url, **kw): self.url = url
    async def evaluate(self, js, url):
        self.calls.append(url)
        resp_url, html = self.responder(url, len(self.calls))
        return {"url": resp_url, "status": 200, "body": html, "ms": 10.0}


def _run(page, art):
    CS.convert_cards = _fake_convert                      # мок fx
    return asyncio.run(CS.fetch_catalog(page, art, zip="19701", condition="new"))


def test_single_page_parsed():
    html = _read("srp_8M0142836_zip19701.html")
    if html is None:
        print("  (skip: нет фикстуры)"); return
    page = FakePage(lambda url, n: (url, html))
    res = _run(page, "8M0142836")
    cat = res.per_query["8M0142836"]
    assert len(cat.items) > 0, len(cat.items)
    assert cat.pages_fetched == 1, cat.pages_fetched     # <240 → стоп после стр.1
    assert len(page.calls) == 1                          # ровно один in-page fetch
    assert all(c.item_id and c.price > 0 for c in cat.items)


def test_empty_search():
    html = _read("srp_empty_09651605.html")
    if html is None:
        print("  (skip)"); return
    page = FakePage(lambda url, n: (url, html))
    cat = _run(page, "09651605").per_query["09651605"]
    assert cat.results_count == 0 and cat.items == []


def test_fewer_words_stop():
    html = _read("srp_no_exact_43881A8.html")
    if html is None:
        print("  (skip)"); return
    page = FakePage(lambda url, n: (url, html))
    cat = _run(page, "43881A8").per_query["43881A8"]
    assert cat.has_fewer_words_sep is True
    assert cat.pages_fetched == 1                         # стоп по сепаратору


def test_pardon_raises():
    page = FakePage(lambda url, n: (url, _PARDON))         # тело Pardon
    try:
        _run(page, "X")
    except PardonError:
        pass
    else:
        raise AssertionError("PardonError ожидался")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
