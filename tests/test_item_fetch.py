"""Тест HTTP-транспорта товара (ebay_library.http.fetch) — офлайн, без сети.

Сетевой curl_cffi подменяем fake-сессией со сценарием ответов/исключений — проверяем
логику ретраев/статусов детерминированно (SPEC.md §7.1/§7.2). Реальный curl_cffi на
живых данных проверен отдельно (этап 2: 40/40, без impersonate, en-US).

Запуск: ``PYTHONPATH=. python3 tests/test_item_fetch.py``.
"""

from __future__ import annotations

import asyncio

from curl_cffi.requests import exceptions as cex

from ebay_library.errors import TransportError
from ebay_library.http import fetch as F


class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text


class _Session:
    """Отдаёт по очереди элементы script; Exception — бросает, иначе возвращает _Resp."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    async def get(self, url):
        item = self._script[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


def _run(coro):
    return asyncio.run(coro)


def test_200_returns_text():
    s = _Session([_Resp(200, "<html>ok</html>")])
    assert _run(F.fetch_item(s, "1")) == "<html>ok</html>"
    assert s.calls == 1


def test_503_then_200_retries():
    s = _Session([_Resp(503), _Resp(200, "good")])
    assert _run(F.fetch_item(s, "1")) == "good"
    assert s.calls == 2


def test_network_error_then_200_retries():
    s = _Session([cex.ConnectionError("boom"), _Resp(200, "good")])
    assert _run(F.fetch_item(s, "1")) == "good"
    assert s.calls == 2


def test_retries_exhausted_raises():
    s = _Session([_Resp(503), _Resp(503), _Resp(503)])
    try:
        _run(F.fetch_item(s, "1"))
    except TransportError:
        assert s.calls == F._RETRIES
    else:
        raise AssertionError("TransportError ожидался (исчерпаны ретраи)")


def test_unexpected_status_raises():
    """404 (и любой непойманный статус) → TransportError сразу, без ретраев."""
    s = _Session([_Resp(404)])
    try:
        _run(F.fetch_item(s, "1"))
    except TransportError:
        assert s.calls == 1
    else:
        raise AssertionError("TransportError ожидался (неожиданный статус)")


def test_description_uses_same_transport():
    s = _Session([_Resp(200, "<desc/>")])
    assert _run(F.fetch_description(s, "1")) == "<desc/>"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
