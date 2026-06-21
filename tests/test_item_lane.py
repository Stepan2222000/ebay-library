"""Тест сборки item-лейна (ebay_library.item.fetch_item_page) — офлайн, без сети.

Сетевую сессию подменяем fake-сессией (отдаёт по URL канон itm/itmdesc) — проверяем
порядок (§4.2), что описание подцепляется (§4.5), и что сбой — транспорта ИЛИ
описания — критичен (по жёсткому, §7). Реальная сборка проверена живьём (этап 3: 30/30).

Запуск: ``PYTHONPATH=. python3 tests/test_item_lane.py``.
"""

from __future__ import annotations

import asyncio
import json
import os

from curl_cffi.requests import exceptions as cex

from ebay_library.errors import ParseError, TransportError
from ebay_library.item import fetch_item_page

_SAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "item_modules_sample.json")


def _itm_html() -> str:
    with open(_SAMPLE, encoding="utf-8") as f:
        modules = json.load(f)
    return ('<html><body><script>window.__data={"modules":' + json.dumps(modules) + "};"
            'var s={"sellerUserName":"fltoolbox"};</script></body></html>')


class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text


class _Session:
    """Скрипт ответов отдельно для itm и itmdesc (по URL); Exception — бросает."""

    def __init__(self, itm, desc):
        self._itm = list(itm)
        self._desc = list(desc)
        self.itm_calls = 0
        self.desc_calls = 0

    async def get(self, url):
        if "/itmdesc/" in url:
            item = self._desc[self.desc_calls]; self.desc_calls += 1
        else:
            item = self._itm[self.itm_calls]; self.itm_calls += 1
        if isinstance(item, Exception):
            raise item
        return item


def _run(coro):
    return asyncio.run(coro)


def test_assembles_itempage_with_description():
    s = _Session(itm=[_Resp(200, _itm_html())],
                 desc=[_Resp(200, "<html><body>Hello desc</body></html>")])
    it = _run(fetch_item_page(s, "277574984378"))
    assert it.item_number == "277574984378"
    assert it.seller == "fltoolbox"
    assert it.description == "Hello desc"          # описание подцепилось (§4.5)
    assert it.price_usd is None and it.shipping_cost is None  # Mode 1 (§4.4)
    assert s.itm_calls == 1 and s.desc_calls == 1  # ровно по одному запросу каждого


def test_item_transport_failure_propagates():
    """Основной запрос исчерпал ретраи → TransportError наружу (критично)."""
    s = _Session(itm=[_Resp(503), _Resp(503), _Resp(503)], desc=[])
    try:
        _run(fetch_item_page(s, "1"))
    except TransportError:
        assert s.desc_calls == 0                   # описание не тянем, если основной упал
    else:
        raise AssertionError("TransportError ожидался")


def test_description_failure_is_critical():
    """Сбой itmdesc критичен (по жёсткому) — TransportError наружу, товар не пишем."""
    s = _Session(itm=[_Resp(200, _itm_html())],
                 desc=[cex.ConnectionError("x"), cex.ConnectionError("x"), cex.ConnectionError("x")])
    try:
        _run(fetch_item_page(s, "1"))
    except TransportError:
        pass
    else:
        raise AssertionError("TransportError ожидался при сбое описания")


def test_non_listing_raises_parseerror():
    """Не-листинг (нет JSON-модели) → ParseError (ended добавим позже)."""
    s = _Session(itm=[_Resp(200, "<html><body>no modules</body></html>")],
                 desc=[_Resp(200, "")])
    try:
        _run(fetch_item_page(s, "1"))
    except ParseError as e:
        assert e.field == "modules"
    else:
        raise AssertionError("ParseError ожидался")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
