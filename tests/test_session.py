"""T8 — EbaySession: контур замены страниц. Прогон: PYTHONPATH=. python3 tests/test_session.py

Фейковый page реализует ровно те методы Playwright, которые дёргают
session/readiness; контент — реальные фикстуры. Сеть нужна только fx-эндпоинту
(конвертация карточек каталога), браузер не нужен.

Покрытие:
  - продолжение каталога с упавшей _pgn без потери карточек (дедуп жив);
  - замена при блоке прямо на прогреве (цепочка замен без лимита);
  - пауза перед КАЖДОЙ заменой и отсутствие паузы перед первой страницей;
  - item повторяется целиком на новой странице;
  - фатальные (Error Page, ZIP-мисматч) — наружу сразу, без запроса страниц;
  - _page_dead на реальных исключениях Playwright;
  - дедуп дубль-запросов, универсальный вход (str | list).
"""

import asyncio
from pathlib import Path

from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeoutError
from playwright._impl._errors import TargetClosedError

import ebaylib.browser.session as sess_mod
from ebaylib import EbaySession, ErrorPageError, ParseError, AccessDeniedError
from ebaylib.browser.session import _page_dead, _retryable

FIX = Path(__file__).parent / "fixtures"
SRP_60 = (FIX / "srp_8M6000623_enUS.html").read_text(encoding="utf-8", errors="replace")
SRP_10 = (FIX / "srp_8M0142836_zip19701.html").read_text(encoding="utf-8", errors="replace")
ITEM_RAW = (FIX / "item_277574984378.html").read_text(encoding="utf-8", errors="replace")
# фикстура снята с EU-сессии (shipTo "00-001"); для happy-path подменяем на наш ZIP
ITEM_OK = ITEM_RAW.replace('"shipToLocation":"00-001"', '"shipToLocation":"19701%2CUSA"')
assert ITEM_OK != ITEM_RAW
DESC = "<html><body><p>Professionally packaged</p></body></html>"

# шаги сценария фейковой страницы (на последовательные goto)
HOME = dict(title="Electronics, Cars, Fashion, Collectibles & More | eBay")
DENIED = dict(title="Access Denied")
ERRPAGE = dict(title="Error Page | eBay")
def SRP(html): return dict(title="x for sale | eBay", content=html)
def ITEM(html): return dict(title="item | eBay", content=html, frames=True)
def DEAD(): return dict(raise_=PWError("Page.goto: net::ERR_CONNECTION_CLOSED at https://x/"))


class FakeFrame:
    url = "https://itm.ebaydesc.com/itmdesc/123"
    async def wait_for_load_state(self, state, timeout=None): pass
    async def content(self): return DESC


class FakeLocator:
    async def scroll_into_view_if_needed(self): pass


class FakePage:
    def __init__(self, name, script):
        self.name, self.script = name, list(script)
        self.gotos = []
        self.url = "about:blank"
        self._title, self._content, self._frames = "", "", []

    async def goto(self, url, wait_until=None):
        assert self.script, f"{self.name}: script exhausted at goto {url}"
        step = self.script.pop(0)
        self.gotos.append(url)
        if "raise_" in step:
            raise step["raise_"]
        self.url = url
        self._title = step["title"]
        self._content = step.get("content", "")
        self._frames = [FakeFrame()] if step.get("frames") else []

    async def title(self): return self._title
    async def content(self): return self._content
    async def wait_for_load_state(self, state): pass
    async def wait_for_selector(self, sel, state=None, timeout=None): pass
    async def wait_for_timeout(self, ms): await asyncio.sleep(0)
    def locator(self, sel): return FakeLocator()
    @property
    def frames(self): return self._frames


def feeder(pages):
    it = iter(pages)
    calls = []
    async def get_page():
        p = next(it)
        calls.append(p.name)
        return p
    return get_page, calls


def test_page_dead_classification():
    # снято живьём (см. specs/page_readiness.md): что лечится заменой, что нет
    assert _page_dead(PWError("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://x/"))
    assert _page_dead(PWError("Page.content: Target crashed "))
    assert _page_dead(TargetClosedError("Page.goto: Target page, context or browser has been closed"))
    assert not _page_dead(PWTimeoutError("Page.goto: Timeout 30000ms exceeded."))  # таймаут критичен
    assert not _page_dead(ValueError("x"))
    assert not _page_dead(TimeoutError("description iframe not loaded"))
    assert _retryable(AccessDeniedError("Access Denied at https://x/"))
    assert not _retryable(ErrorPageError("Error Page at https://x/"))
    assert not _retryable(ParseError("seller", None, "1", "<html>"))


def test_catalog_resumes_from_failed_page():
    # A: warmup, p1 (60 карточек = полная при патче лимита), p2 → denied;
    # B: warmup, p2 (10 карточек + сепаратор) → стоп. Продолжаем с p2, не с p1.
    sess_mod.ITEMS_PER_PAGE = 60  # только для теста: полная страница = фикстура
    try:
        pages = [
            FakePage("A", [HOME, SRP(SRP_60), DENIED]),
            FakePage("B", [HOME, SRP(SRP_10)]),
        ]
        get_page, calls = feeder(pages)
        s = EbaySession(get_page, page_delay_s=0.2)
        loop = asyncio.new_event_loop()
        t0 = loop.time()
        res = loop.run_until_complete(s.fetch_catalog("8M6000623", zip="19701"))
        dt = loop.time() - t0
        loop.close()
        cat = res.per_query["8M6000623"]
        assert calls == ["A", "B"], calls
        assert cat.pages_fetched == 2 and cat.results_count == 273, cat
        assert cat.has_fewer_words_sep is True
        assert len(cat.items) == 70 and len(res.items) == 70, len(cat.items)
        # упали на p2 — оба последних goto именно p2 (продолжение, не рестарт)
        assert "_pgn=2" in pages[0].gotos[-1], pages[0].gotos[-1]
        assert "_pgn=2" in pages[1].gotos[-1], pages[1].gotos[-1]
        assert dt >= 0.2, dt  # пауза перед заменой выдержана
    finally:
        sess_mod.ITEMS_PER_PAGE = 240


def test_replacement_chain_and_delays():
    # net::ERR на SRP → замена; блок на прогреве новой страницы → ещё замена.
    # Паузы: перед B и перед C (две), перед первой страницей — нет.
    pages = [
        FakePage("A", [HOME, DEAD()]),
        FakePage("B", [DENIED]),                       # Access Denied прямо на главной
        FakePage("C", [HOME, SRP(SRP_10)]),
    ]
    get_page, calls = feeder(pages)
    s = EbaySession(get_page, page_delay_s=0.2)
    loop = asyncio.new_event_loop()
    t0 = loop.time()
    res = loop.run_until_complete(s.fetch_catalog(["8M0142836"], zip="19701"))
    dt = loop.time() - t0
    loop.close()
    assert calls == ["A", "B", "C"], calls
    assert len(res.items) == 10
    assert dt >= 0.4, dt  # две замены × 0.2с


def test_no_delay_before_first_page():
    # happy-path без замен: при page_delay_s=5.0 должен пройти мгновенно
    pages = [FakePage("A", [HOME, SRP(SRP_10)])]
    get_page, calls = feeder(pages)
    s = EbaySession(get_page, page_delay_s=5.0)
    loop = asyncio.new_event_loop()
    t0 = loop.time()
    res = loop.run_until_complete(s.fetch_catalog("8M0142836", zip="19701"))
    dt = loop.time() - t0
    loop.close()
    assert len(res.items) == 10 and calls == ["A"]
    assert dt < 2.0, dt  # паузы не было (запас на fx-сеть)


def test_item_retried_whole_on_new_page():
    pages = [
        FakePage("A", [HOME, DEAD()]),
        FakePage("B", [HOME, ITEM(ITEM_OK)]),
    ]
    get_page, calls = feeder(pages)
    s = EbaySession(get_page, page_delay_s=0.05)
    it = asyncio.run(s.fetch_item("277574984378", zip="19701"))
    assert calls == ["A", "B"], calls
    assert it.item_number == "277574984378"
    assert it.price_usd == 47.50 and it.seller == "fltoolbox"
    assert it.description == "Professionally packaged", repr(it.description)


def test_fatal_error_page_no_replacement():
    pages = [FakePage("A", [HOME, ERRPAGE])]
    get_page, calls = feeder(pages)
    s = EbaySession(get_page, page_delay_s=0.05)
    try:
        asyncio.run(s.fetch_catalog("q", zip="19701"))
    except ErrorPageError:
        pass
    else:
        raise AssertionError("expected ErrorPageError")
    assert calls == ["A"], calls  # новую страницу не просили


def test_fatal_zip_mismatch_after_setter():
    # фикстура с чужим shipTo: заход → мисматч → setter-SRP → заход → мисматч → ParseError
    pages = [FakePage("A", [HOME, ITEM(ITEM_RAW), SRP(SRP_10), ITEM(ITEM_RAW)])]
    get_page, calls = feeder(pages)
    s = EbaySession(get_page, page_delay_s=0.05)
    try:
        asyncio.run(s.fetch_item("277574984378", zip="19701"))
    except ParseError as e:
        assert e.field == "ship_to_location" and e.raw == "00-001", (e.field, e.raw)
    else:
        raise AssertionError("expected ParseError")
    assert calls == ["A"] and len(pages[0].gotos) == 4, (calls, pages[0].gotos)


def test_duplicate_queries_and_str_input():
    # дубль-запрос на eBay не ходит дважды; вход строкой эквивалентен списку
    pages = [FakePage("A", [HOME, SRP(SRP_10)])]
    get_page, _ = feeder(pages)
    s = EbaySession(get_page, page_delay_s=0.05)
    res = asyncio.run(s.fetch_catalog(["8M0142836", "8M0142836"], zip="19701"))
    assert list(res.per_query) == ["8M0142836"]
    assert len([u for u in pages[0].gotos if "/sch/" in u]) == 1, pages[0].gotos


if __name__ == "__main__":
    test_page_dead_classification()
    test_catalog_resumes_from_failed_page()
    test_replacement_chain_and_delays()
    test_no_delay_before_first_page()
    test_item_retried_whole_on_new_page()
    test_fatal_error_page_no_replacement()
    test_fatal_zip_mismatch_after_setter()
    test_duplicate_queries_and_str_input()
    print("PASS")
