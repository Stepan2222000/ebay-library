"""Слой 2 — ``EbaySession``: добыча данных на живых Playwright ``page``.

Сессия не получает готовый page — она получает ``get_page`` (async-колбек
воркера «дай свежую страницу»; браузер/прокси/стелс — зона воркера) и сама:

- лениво берёт первую страницу и прогревает её (заход на главную — без
  прогрева первый SRP ловит антифрод);
- при блокировке eBay (Access Denied) или транспортной смерти страницы
  (net::ERR_*/краш/закрытие) выдерживает паузу ``page_delay_s``, просит у
  воркера новую страницу, прогревает и продолжает С ТОГО ЖЕ МЕСТА: каталог —
  с упавшей страницы выдачи (собранное не теряется), item — товар целиком.
  Лимита замен нет — темп и счёт попыток контролирует воркер внутри
  ``get_page`` (может ждать сколько угодно или бросить исключение — оно
  критическое).

Всё остальное — критические ошибки: летят наружу и валят задачу и воркера
целиком (Pardon не прошёл за таймаут, Error Page, ParseError, сбой fx,
таймауты якорей/iframe-описания, неопознанное). Машинерии «мёртвой сессии»
нет: после критического исключения воркер завершается, объект сессии умирает
вместе с ним.

Контракт ``get_page``: каждый вызов обязан отдавать СВЕЖУЮ страницу (новый
контекст/прокси) — вернёт ту же, и замены зациклятся на том же блоке (не
проверяем, это зона воркера). Старые page библиотека не закрывает —
утилизирует воркер.

Диагностика — стандартный logging (логгер "ebaylib"): INFO при заменах
страниц (с причиной и местом), DEBUG на прогрессе. Без хендлера — молчит.
"""

from __future__ import annotations

import asyncio
import logging

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..errors import AccessDeniedError, ParseError
from ..html.item import parse_item_page, ship_to_location
from ..html.page_state import PageKind
from ..html.srp import parse_search_page
from ..http.fx import convert_cards
from ..models import Catalog, CatalogItem, CatalogResult, ItemPage, SrpCard
from ..urls import ITEMS_PER_PAGE, build_search_url
from .readiness import wait_until_ready

logger = logging.getLogger("ebaylib")

_HOME_URL = "https://www.ebay.com/"
_ITEM_URL = "https://www.ebay.com/itm/{item_id}"
_DESC_FRAME_HOST = "ebaydesc.com"
DESC_TIMEOUT_S = 15.0
PAGE_DELAY_S = 0.5   # фикс. пауза перед каждым replacement-запросом новой страницы
MAX_PAGES = 5        # лимит страниц выдачи на запрос (дефолт fetch_catalog)


def _page_dead(exc: BaseException) -> bool:
    """Транспортная смерть страницы — лечится только новым page.

    Снято живьём (Playwright 1.60): net::ERR_* → Error с 'net::ERR' в
    сообщении; закрытые page/context/browser → TargetClosedError (наследник
    Error; из ``playwright.async_api`` не экспортируется — матчим по имени
    типа); операции на крашнутой странице → Error 'Target crashed'.
    TimeoutError (тоже наследник Error) — НЕ транспорт: таймауты критичны."""
    if not isinstance(exc, PlaywrightError) or isinstance(exc, PlaywrightTimeoutError):
        return False
    if type(exc).__name__ == "TargetClosedError":
        return True
    msg = str(exc)
    return "net::ERR" in msg or "Target crashed" in msg


def _retryable(exc: BaseException) -> bool:
    """Что лечится заменой страницы: блокировка eBay либо смерть транспорта.
    Всё остальное — критично (наружу)."""
    return isinstance(exc, AccessDeniedError) or _page_dead(exc)


class EbaySession:
    """Сессия воркера: один живой page за раз, замена при блоке/смерти.

    ``get_page`` — async-колбек без аргументов → свежий Playwright ``page``
    (контракт — в шапке модуля). ``page_delay_s`` — фиксированная пауза перед
    каждым replacement-запросом (перед самым первым получением страницы паузы
    нет). Остальные тайминги — константы readiness/session (свойства eBay,
    не развёртывания).

    Методы не потокобезопасны: одна сессия = один page = последовательные
    вызовы (как и сам Playwright page).
    """

    def __init__(self, get_page, *, page_delay_s: float = PAGE_DELAY_S):
        self._get_page = get_page
        self._page_delay_s = page_delay_s
        self._page = None

    async def _warmup(self, page) -> None:
        """Прогрев: главная eBay + готовность (без него первый SRP ловит антифрод)."""
        await page.goto(_HOME_URL, wait_until="domcontentloaded")
        await wait_until_ready(page, PageKind.HOME)
        logger.debug("warmup ok")

    async def _acquire(self, *, replacement: bool):
        """Берёт у воркера страницу и прогревает. Блок/смерть на самом прогреве
        → следующая замена (без лимита). Пауза — перед каждым replacement-
        запросом (блок только что был, даём остыть)."""
        while True:
            if replacement:
                await asyncio.sleep(self._page_delay_s)
            replacement = True
            page = await self._get_page()
            try:
                await self._warmup(page)
            except Exception as e:
                if not _retryable(e):
                    raise
                logger.info(
                    "warmup blocked (%s: %.200s) — requesting another page",
                    type(e).__name__, e,
                )
                continue
            return page

    async def _run(self, unit, *, what: str):
        """Крутит ``unit(page)`` до успеха: ретрай (замена страницы) только на
        блокировке/смерти транспорта, всё остальное — критически наружу."""
        if self._page is None:
            self._page = await self._acquire(replacement=False)
        while True:
            try:
                return await unit(self._page)
            except Exception as e:
                if not _retryable(e):
                    raise
                logger.info(
                    "page lost at %s (%s: %.200s) — replacing",
                    what, type(e).__name__, e,
                )
                self._page = await self._acquire(replacement=True)

    # ------------------------------------------------------------- каталог

    async def fetch_catalog(
        self,
        queries: str | list[str],
        *,
        zip: str | None = None,
        condition: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        max_pages: int = MAX_PAGES,
    ) -> CatalogResult:
        """Каталоги по запросу или списку запросов — один универсальный вход.

        По каждому запросу проходит страницы выдачи (стоп: сепаратор
        fewer-words, неполная страница либо лимит ``max_pages`` на запрос —
        по умолчанию 5; нахлёст соседних страниц гасится дедупом по item_id),
        после сбора страниц запроса один fx-батч переводит цены/доставку в
        USD. Дубликаты запросов отбрасываются (порядок сохраняется, на eBay
        повторно не ходим).

        Фильтры (``zip``/``condition``/``min_price``/``max_price``) задаются
        через URL на каждой странице и применяются ко всем запросам; ``zip``
        на практике нужен всегда (рекоменд. "19701") — без него доставка не
        рендерится и парсер падает.

        Итог — ``CatalogResult``: общий список карточек с глобальным дедупом
        по item_id по всем запросам (первое вхождение побеждает) +
        ``per_query`` с каталогом каждого запроса.

        Блокировка/смерть страницы лечится внутри (замена + продолжение с
        упавшей страницы выдачи). Критические ошибки (ParseError, Error Page,
        Pardon-таймаут, сбой fx…) валят вызов целиком — частичных результатов
        нет, переотдача задачи — забота оркестратора."""
        qlist = [queries] if isinstance(queries, str) else list(dict.fromkeys(queries))
        filters = dict(zip=zip, condition=condition, min_price=min_price, max_price=max_price)

        items: list[CatalogItem] = []
        seen: set[str] = set()
        per_query: dict[str, Catalog] = {}

        for query in qlist:
            cards: list[SrpCard] = []
            qseen: set[str] = set()
            results_count = 0
            pages_fetched = 0
            has_sep = False
            pgn = 1
            while True:
                url = build_search_url(query, page=pgn, **filters)

                async def one_srp(page, url=url):
                    await page.goto(url, wait_until="domcontentloaded")
                    await wait_until_ready(page, PageKind.SRP)
                    return parse_search_page(await page.content())

                sp = await self._run(one_srp, what=f"srp {query!r} pgn={pgn}")
                pages_fetched += 1
                logger.debug("srp %r pgn=%d: %d cards", query, pgn, len(sp.items))
                if pgn == 1:
                    results_count = sp.results_count
                has_sep = has_sep or sp.has_fewer_words_sep
                for c in sp.items:
                    if c.item_id not in qseen:
                        qseen.add(c.item_id)
                        cards.append(c)
                # Стоп: дошли до «похожих» (fewer-words), страница неполная
                # (последняя; eBay за концом не отдаёт пустую — клампит) либо
                # упёрлись в лимит страниц на запрос.
                if sp.has_fewer_words_sep or len(sp.items) < ITEMS_PER_PAGE:
                    break
                if pgn >= max_pages:
                    logger.debug("srp %r: page cap %d reached", query, max_pages)
                    break
                pgn += 1

            # Один fx-батч на каталог запроса; сбой fx — критически наружу.
            converted = await convert_cards(cards)
            per_query[query] = Catalog(
                query=query,
                results_count=results_count,
                items=converted,
                pages_fetched=pages_fetched,
                has_fewer_words_sep=has_sep,
            )
            for it in converted:
                if it.item_id not in seen:
                    seen.add(it.item_id)
                    items.append(it)

        return CatalogResult(items=items, per_query=per_query)

    # ---------------------------------------------------------------- item

    async def fetch_item(
        self, item_id: str, *, zip: str, desc_timeout_s: float = DESC_TIMEOUT_S
    ) -> ItemPage:
        """Страница товара → ``ItemPage`` (основной HTML + iframe-описание).

        ``zip`` обязателен: без ship-to локации eBay считает её по IP-гео
        (на ``/itm/`` URL-параметр ``_stpos`` игнорируется, локация
        сессионная). Поток оптимистичный: сразу на товар, сверка
        ``shipToLocation`` в его же HTML; мискматч → один setter-визит SRP
        (``_nkw`` = item_id, ``_stpos={zip}``; даже «0 results» выставляет
        ZIP на всю сессию) → повторный заход; повторный мискматч → ParseError.
        Стационарно (ZIP уже стоит) — ноль лишних навигаций. См.
        specs/item_flow.md.

        Блокировка/смерть страницы лечится внутри (замена + повтор товара
        целиком, включая ZIP-флоу). Критические: ParseError (поля,
        ship_to_location), TimeoutError (Pardon/якоря/iframe-описание),
        Error Page."""
        expected = f"{zip},USA"

        async def one_item(page) -> ItemPage:
            for is_retry in (False, True):
                await page.goto(
                    _ITEM_URL.format(item_id=item_id), wait_until="domcontentloaded"
                )
                await wait_until_ready(page, PageKind.ITEM)
                main_html = await page.content()
                actual = ship_to_location(main_html)
                if actual == expected:
                    break
                if is_retry:
                    raise ParseError("ship_to_location", actual, item_id, main_html)
                logger.debug("zip mismatch (%s != %s) — setter SRP visit", actual, expected)
                await page.goto(
                    build_search_url(item_id, page=1, zip=zip),
                    wait_until="domcontentloaded",
                )
                await wait_until_ready(page, PageKind.SRP)
            description_html = await _description_html(page, desc_timeout_s)
            return parse_item_page(main_html, description_html)

        return await self._run(one_item, what=f"item {item_id}")


async def _description_html(page, timeout_s: float) -> str:
    """HTML iframe-описания. Тег #desc_ifr уже в DOM (гарантировано готовностью),
    но документ фрейма ЛЕНИВЫЙ — eBay начинает грузить его только при попадании
    iframe во viewport (подтверждено live: без скролла не грузится вовсе),
    поэтому сперва скроллим к нему. Затем ждём фрейм с host ebaydesc.com +
    domcontentloaded. Нет за таймаут → TimeoutError, критично (заменой страницы
    не лечим: медленный-но-живой прокси — сигнал чинить пул воркера)."""
    await page.locator("#desc_ifr").scroll_into_view_if_needed()
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        frame = next((f for f in page.frames if _DESC_FRAME_HOST in f.url), None)
        if frame:
            await frame.wait_for_load_state(
                "domcontentloaded", timeout=(deadline - loop.time()) * 1000
            )
            return await frame.content()
        await page.wait_for_timeout(300)
    raise TimeoutError(f"description iframe not loaded at {page.url}")
