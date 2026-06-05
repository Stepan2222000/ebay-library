"""Слой 2 — добыча данных на живом Playwright ``page`` (методы воркера).

Воркер даёт прогретый ``page`` (прокси/стелс/прогрев главной — вне библиотеки).
Здесь: навигация на нужный URL, ожидание готовности (navigation.wait_until_ready),
сбор HTML и передача в чистые парсеры (Слой 1).

``fetch_item`` собирает ДВА HTML: основной (поля) + iframe-описание, и отдаёт
их в ``parse_item_page``.
"""

from __future__ import annotations

import asyncio

from ..config import EbayConfig
from ..parsing.catalog import parse_search_page
from ..parsing.item import parse_item_page
from ..parsing.models import Catalog, CatalogBatch, CatalogItem, ItemPage
from ..parsing.page_state import PageKind
from .navigation import wait_until_ready
from .zipcode import set_zip

_HOME_URL = "https://www.ebay.com/"
_ITEM_URL = "https://www.ebay.com/itm/{item_id}"
_DESC_FRAME_HOST = "ebaydesc.com"
DESC_TIMEOUT_S = 15.0


async def warmup(page) -> None:
    """Старт сессии: заход на главную eBay и ожидание готовности. Воркер зовёт
    ОДИН раз в начале (без прогрева главной первый SRP ловит антифрод/Error Page).
    Дальнейшие fetch_catalog(s)/fetch_item на главную не возвращаются."""
    await page.goto(_HOME_URL, wait_until="domcontentloaded")
    await wait_until_ready(page, PageKind.HOME)


async def _description_html(page, timeout_s: float) -> str:
    """HTML iframe-описания. Тег #desc_ifr уже в DOM (гарантировано готовностью),
    но документ фрейма грузится отдельно: сначала about:blank, затем реальный
    ebaydesc.com. Ждём фрейм с этим host + domcontentloaded. Нет за таймаут →
    фатально (TimeoutError), как договорено."""
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


async def fetch_item(page, item_id: str, desc_timeout_s: float = DESC_TIMEOUT_S) -> ItemPage:
    """Открывает страницу товара на ``page``, дожидается готовности, собирает
    основной HTML + HTML iframe-описания и парсит в ItemPage.

    Бросает AccessDeniedError / ErrorPageError / TimeoutError (готовность),
    TimeoutError (iframe), ParseError (поля)."""
    await page.goto(_ITEM_URL.format(item_id=item_id), wait_until="domcontentloaded")
    await wait_until_ready(page, PageKind.ITEM)
    main_html = await page.content()
    description_html = await _description_html(page, desc_timeout_s)
    return parse_item_page(main_html, description_html)


async def _fetch_srp(page, url: str):
    """Открывает SRP-url, дожидается готовности, парсит одну страницу выдачи."""
    await page.goto(url, wait_until="domcontentloaded")
    await wait_until_ready(page, PageKind.SRP)
    return parse_search_page(await page.content())


async def fetch_catalog(page, query: str, config: EbayConfig | None = None) -> Catalog:
    """Собирает весь каталог по одной подзадаче (поисковому запросу): проходит
    страницы выдачи, склеивает карточки (дедуп по item_id, порядок сохранён).

    ZIP ставится один раз перед первой страницей (доставка в карточках от него
    зависит; set_zip идемпотентен). Идём по страницам, пока страница ПОЛНАЯ
    (ровно items_per_page карточек). Неполная страница (< items_per_page) —
    последняя (eBay за концом не отдаёт пустую, а клампит на последнюю — поэтому
    стоп по «<240», а не по «0 карточек»). Между соседними страницами выдача
    нахлёстывается — спасает дедуп по item_id. Ранний стоп — сепаратор
    fewer-words. Счётчик результатов справочный, циклом не управляет. Любой сбой
    пробрасывается наружу — подзадача падает целиком (в блоке её изолирует
    fetch_catalogs)."""
    config = config or EbayConfig()
    ipg = config.items_per_page

    # Первый заход на SRP — только дождаться готовности и выставить ZIP, БЕЗ
    # парсинга: без ZIP у части карточек доставка не показана ("Shipping not
    # specified") и парс упал бы (подтверждено live). ZIP — кука контекста.
    await page.goto(config.search_url(query, 1), wait_until="domcontentloaded")
    await wait_until_ready(page, PageKind.SRP)
    await set_zip(page, config)

    items: list[CatalogItem] = []
    seen: set[str] = set()

    def add(page_items: list[CatalogItem]) -> None:
        for it in page_items:
            if it.item_id not in seen:
                seen.add(it.item_id)
                items.append(it)

    results_count = 0
    pages_fetched = 0
    has_sep = False
    pgn = 1
    while True:
        sp = await _fetch_srp(page, config.search_url(query, pgn))
        pages_fetched += 1
        if pgn == 1:
            results_count = sp.results_count
        has_sep = has_sep or sp.has_fewer_words_sep
        add(sp.items)
        # Стоп: дошли до «похожих» (fewer-words) либо страница неполная (последняя).
        if sp.has_fewer_words_sep or len(sp.items) < ipg:
            break
        pgn += 1

    return Catalog(
        query=query,
        results_count=results_count,
        items=items,
        pages_fetched=pages_fetched,
        has_fewer_words_sep=has_sep,
    )


async def fetch_catalogs(
    page, queries: list[str], config: EbayConfig | None = None
) -> CatalogBatch:
    """Парсит блок подзадач (список запросов) на одном ``page``, возвращает
    объединённый список карточек без дублей.

    Подзадачи идут по порядку. Упавшая подзадача НЕ валит блок — её ошибка
    попадает в ``errors`` (оркестратор переотдаст позже), остальные продолжают.
    Дедуп item_id глобальный по всему блоку (первое вхождение побеждает): один
    товар, найденный по разным запросам, в общий список идёт один раз. ZIP
    ставится при первой подзадаче (внутри fetch_catalog, идемпотентно)."""
    config = config or EbayConfig()
    items: list[CatalogItem] = []
    seen: set[str] = set()
    per_query: dict[str, Catalog] = {}
    errors: dict[str, str] = {}

    for query in queries:
        try:
            cat = await fetch_catalog(page, query, config)
        except Exception as e:  # изолируем сбой подзадачи — блок не падает
            errors[query] = f"{type(e).__name__}: {e}"
            continue
        per_query[query] = cat
        for it in cat.items:
            if it.item_id not in seen:
                seen.add(it.item_id)
                items.append(it)

    return CatalogBatch(items=items, per_query=per_query, errors=errors)
