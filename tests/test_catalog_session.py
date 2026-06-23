"""Тест каталог-сессии (ebay_library.browser.catalog_session.CatalogSession) — офлайн.

Браузерную страницу подменяем FakePage (``evaluate`` отдаёт канон-ответ по responder),
``convert_cards`` мокаем (без fx-сети). Покрываем парсинг (фикстуры) и КОНТУР
ВОССТАНОВЛЕНИЯ (event+epoch + ready-ворота + reload-на-главную). ⚠️ Это проверка ЛОГИКИ
на моке — НЕ гарантия живого поведения (антибот живьём не вызывается, см. шапку
catalog_session.py). Запуск: ``PYTHONPATH=. python3 tests/test_catalog_session.py``.
"""

from __future__ import annotations

import asyncio
import os

import ebay_library.browser.catalog_session as CS
from ebay_library.browser.catalog_session import CatalogSession
from ebay_library.errors import ErrorPageError
from ebay_library.models import CatalogItem
from ebay_library.urls import build_search_url

_FIX = os.path.join(os.path.dirname(__file__), "fixtures")
SRP_URL = build_search_url("X", page=1, zip="19701", condition="new")


def _read(name):
    p = os.path.join(_FIX, name)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


def _srp():    return {"url": SRP_URL, "status": 200, "body": "<html><title>X | eBay</title></html>", "ms": 1.0}
def _pardon(): return {"url": "https://www.ebay.com/splashui/challenge?x", "status": 200, "body": "<title>Pardon Our Interruption</title>", "ms": 1.0}
def _denied(): return {"url": SRP_URL, "status": 200, "body": "<title>Access Denied</title>", "ms": 1.0}
def _errpg():  return {"url": SRP_URL, "status": 200, "body": "<title>Error Page | eBay</title>", "ms": 1.0}
def _home():   return {"url": "https://www.ebay.com/", "status": 200, "body": "<title>eBay</title>", "ms": 1.0}
FAILED = RuntimeError("Page.evaluate: TypeError: Failed to fetch")   # обрыв/сбой — неотличимы по тексту
CRASH = RuntimeError("Page.evaluate: Target crashed")


async def _fake_convert(cards, **kw):
    return [CatalogItem(item_id=c.item_id, title=c.title, condition=c.condition, price=c.price,
                        shipping_cost=c.shipping_cost, seller=c.seller, location=c.location,
                        image_url=c.image_url) for c in cards]


class FakeCtrl:
    def __init__(self): self.backoffs = 0; self.resets = 0
    def backoff_now(self): self.backoffs += 1; return 1
    def reset(self): self.resets += 1; return 1


class FakePage:
    """responder(session, call_n) → dict | BaseException. goto_gate/goto_raises — на reload
    (1-я goto = warmup, 2+ = починка)."""
    def __init__(self, responder, *, name="p", session_ref=None, goto_raises=None, goto_gate=None):
        self.responder = responder; self.name = name; self.session_ref = session_ref
        self.goto_raises = goto_raises; self.goto_gate = goto_gate
        self.calls = []; self.gotos = []; self.url = "https://www.ebay.com/"
    async def goto(self, url, **kw):
        await asyncio.sleep(0); self.gotos.append(url)
        is_reload = len(self.gotos) > 1
        if self.goto_gate is not None and is_reload:
            await self.goto_gate.wait()
        if self.goto_raises is not None and is_reload:
            raise self.goto_raises
        self.url = url
    async def evaluate(self, js, url):
        await asyncio.sleep(0); self.calls.append(url)
        s = self.session_ref() if self.session_ref else None
        r = self.responder(s, len(self.calls))
        if isinstance(r, BaseException): raise r
        return r


def _make(responder, *, pages=None, ctrl=None, goto_raises=None, goto_gate=None):
    holder = {}
    page = FakePage(responder, session_ref=lambda: holder["s"], goto_raises=goto_raises, goto_gate=goto_gate)
    plist = pages if pages is not None else [page]
    it = iter(plist)
    async def get_page(): return next(it)
    s = CatalogSession(get_page, controller=ctrl); holder["s"] = s
    return s, plist


def run(coro): return asyncio.run(coro)
def expect_raise(cf, exc, label):
    try: run(cf()); raise AssertionError(f"{label}: ожидалось исключение")
    except exc: pass


# ---------- контур восстановления (event+epoch) ----------
def test_srp_ok():
    s, [p] = _make(lambda sess, n: _srp()); run(s.start())
    assert "eBay" in run(s.fetch_srp(SRP_URL)) and s.epoch == 0 and len(p.calls) == 1


def test_pardon_recover_then_srp():
    ctrl = FakeCtrl()
    s, [p] = _make(lambda sess, n: _pardon() if sess.epoch == 0 else _srp(), ctrl=ctrl); run(s.start())
    assert "eBay" in run(s.fetch_srp(SRP_URL)) and s.epoch == 1 and ctrl.backoffs == 1


def test_pardon_persists_two_recovers():
    ctrl = FakeCtrl()
    s, [p] = _make(lambda sess, n: _pardon() if sess.epoch < 2 else _srp(), ctrl=ctrl); run(s.start())
    assert "eBay" in run(s.fetch_srp(SRP_URL)) and s.epoch == 2 and ctrl.backoffs == 2


def test_access_denied_swap_then_srp():
    ctrl = FakeCtrl()
    p1 = FakePage(lambda s, n: _denied(), name="p1"); p2 = FakePage(lambda s, n: _srp(), name="p2")
    s, _ = _make(None, pages=[p1, p2], ctrl=ctrl); run(s.start())
    assert "eBay" in run(s.fetch_srp(SRP_URL)) and s.page is p2 and ctrl.resets == 1 and s.epoch == 1


def test_access_denied_persists_two_swaps():
    ctrl = FakeCtrl()
    ps = [FakePage(lambda s, n: _denied(), name="p1"), FakePage(lambda s, n: _denied(), name="p2"),
          FakePage(lambda s, n: _srp(), name="p3")]
    s, _ = _make(None, pages=ps, ctrl=ctrl); run(s.start())
    assert "eBay" in run(s.fetch_srp(SRP_URL)) and s.page is ps[2] and ctrl.resets == 2


def test_mixed_pardon_then_denied():
    ctrl = FakeCtrl()
    p1 = FakePage(lambda s, n: _pardon() if n == 1 else _denied(), name="p1")
    p2 = FakePage(lambda s, n: _srp(), name="p2")
    s, _ = _make(None, pages=[p1, p2], ctrl=ctrl); run(s.start())
    assert "eBay" in run(s.fetch_srp(SRP_URL)) and s.page is p2 and ctrl.backoffs == 1 and ctrl.resets == 1


def test_error_page_critical():
    s, _ = _make(lambda s, n: _errpg()); run(s.start())
    expect_raise(lambda: s.fetch_srp(SRP_URL), ErrorPageError, "error page")


def test_unexpected_kind_critical():
    s, _ = _make(lambda s, n: _home()); run(s.start())
    expect_raise(lambda: s.fetch_srp(SRP_URL), ErrorPageError, "unexpected kind")


def test_failed_fetch_epoch_unchanged_critical():
    s, _ = _make(lambda s, n: FAILED); run(s.start())
    expect_raise(lambda: s.fetch_srp(SRP_URL), RuntimeError, "failed fetch")


def test_failed_fetch_epoch_changed_resent():
    def resp(sess, n):
        if n == 1: sess._epoch += 1; return FAILED      # как будто параллельная починка
        return _srp()
    s, _ = _make(resp); run(s.start())
    assert "eBay" in run(s.fetch_srp(SRP_URL))


def test_crash_epoch_unchanged_critical():
    s, _ = _make(lambda s, n: CRASH); run(s.start())
    expect_raise(lambda: s.fetch_srp(SRP_URL), RuntimeError, "crash")


def test_recover_itself_dies_critical():
    s, _ = _make(lambda sess, n: _pardon(), goto_raises=CRASH); run(s.start())
    expect_raise(lambda: s.fetch_srp(SRP_URL), RuntimeError, "recover dies")


def test_concurrent_pardon_single_recover():
    ctrl = FakeCtrl()
    s, [p] = _make(lambda sess, n: _pardon() if sess.epoch == 0 else _srp(), ctrl=ctrl); run(s.start())
    async def many(): return await asyncio.gather(*[s.fetch_srp(SRP_URL) for _ in range(5)])
    r = run(many())
    assert all("eBay" in b for b in r) and s.epoch == 1 and ctrl.backoffs == 1


def test_concurrent_access_denied_single_swap():
    ctrl = FakeCtrl()
    p1 = FakePage(lambda s, n: _denied(), name="p1"); p2 = FakePage(lambda s, n: _srp(), name="p2")
    s, _ = _make(None, pages=[p1, p2], ctrl=ctrl); run(s.start())
    async def many(): return await asyncio.gather(*[s.fetch_srp(SRP_URL) for _ in range(5)])
    r = run(many())
    assert all("eBay" in b for b in r) and s.page is p2 and ctrl.resets == 1


def test_ready_gate_blocks_new_fetch_during_recover():
    gate = asyncio.Event()
    s, [p] = _make(lambda sess, n: _pardon() if sess.epoch == 0 else _srp(), ctrl=FakeCtrl(), goto_gate=gate)
    run(s.start())
    async def scenario():
        a = asyncio.create_task(s.fetch_srp(SRP_URL))
        await asyncio.sleep(0.05)
        b = asyncio.create_task(s.fetch_srp(SRP_URL))
        await asyncio.sleep(0.05)
        waited = not b.done(); calls_before = len(p.calls)
        gate.set()
        ra, rb = await asyncio.gather(a, b)
        return waited, calls_before, ra, rb
    waited, calls_before, ra, rb = run(scenario())
    assert waited and calls_before == 1 and "eBay" in ra and "eBay" in rb


# ---------- парсинг + объединение (фикстуры) ----------
def test_fetch_catalog_single_page():
    html = _read("srp_8M0142836_zip19701.html")
    if html is None: print("  (skip: нет фикстуры)"); return
    CS.convert_cards = _fake_convert
    url = build_search_url("8M0142836", page=1, zip="19701", condition="new")
    s, [p] = _make(lambda sess, n: {"url": url, "status": 200, "body": html, "ms": 1.0}); run(s.start())
    cat = run(s.fetch_catalog("8M0142836", zip="19701", condition="new")).per_query["8M0142836"]
    assert len(cat.items) > 0 and cat.pages_fetched == 1 and all(c.item_id and c.price > 0 for c in cat.items)


def test_fetch_catalog_empty():
    html = _read("srp_empty_09651605.html")
    if html is None: print("  (skip)"); return
    CS.convert_cards = _fake_convert
    url = build_search_url("09651605", page=1, zip="19701", condition="new")
    s, _ = _make(lambda sess, n: {"url": url, "status": 200, "body": html, "ms": 1.0}); run(s.start())
    cat = run(s.fetch_catalog("09651605", zip="19701", condition="new")).per_query["09651605"]
    assert cat.results_count == 0 and cat.items == []


def test_fetch_catalog_with_pardon_recovers():
    html = _read("srp_8M0142836_zip19701.html")
    if html is None: print("  (skip)"); return
    CS.convert_cards = _fake_convert
    url = build_search_url("8M0142836", page=1, zip="19701", condition="new")
    def resp(sess, n):
        return _pardon() if sess.epoch == 0 else {"url": url, "status": 200, "body": html, "ms": 1.0}
    s, [p] = _make(resp, ctrl=FakeCtrl()); run(s.start())
    cat = run(s.fetch_catalog("8M0142836", zip="19701", condition="new")).per_query["8M0142836"]
    assert len(cat.items) > 0 and s.epoch == 1


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
