"""Слой 2 — ожидание готовности страницы на живом Playwright ``page``.

Навигирует сессия (browser/session); эта функция крутит цикл ожидания и
применяет политики блоков; «что за страница» и «готова ли» определяет
html/page_state (по url+title, не по DOM).

  PARDON        — ждём, пока пройдёт сам (до timeout_s); не прошёл → TimeoutError (критично);
  ERROR_PAGE    — ErrorPageError (критично — наружу, задача падает);
  ACCESS_DENIED — AccessDeniedError (лечится заменой страницы — контур в session);
  UNKNOWN url   — не antibot и не целевая: сохраняем HTML для разбора, ждём дальше.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from ..errors import AccessDeniedError, ErrorPageError
from ..html.page_state import Antibot, PageKind, detect_state, is_ready
from ..html.selectors import Home, Item, Srp

PARDON_TIMEOUT_S = 180.0   # хардкап ожидания Pardon = 3 минуты
ANCHOR_TIMEOUT_S = 30.0    # ожидание появления DOM-якорей целевой страницы
_POLL_S = 1.0
UNKNOWN_DUMP_DIR = Path("ebay_data/unknown")

# DOM-якоря «готовности» по типу страницы: всё, что мы планируем парсить, должно
# присутствовать в DOM. page_state остаётся чистым (url+title); сами селекторы
# берём из selectors.py (DRY). Подтверждено live.
READY_ANCHORS: dict[PageKind, tuple[str, ...]] = {
    PageKind.HOME: (
        Home.SEARCH_BOX,
    ),
    PageKind.SRP: (
        Srp.COUNT_HEADING,
        Srp.CARD,
    ),
    PageKind.ITEM: (
        Item.TITLE,
        Item.ITEM_NUMBER,
        Item.PRICE_PRIMARY,
        Item.CONDITION,
        # блок «Shipping…» содержит ЛИБО строку доставки, ЛИБО самовывоз —
        # ждём любую (CSS-альтернатива через запятую)
        f"{Item.SHIPPING}, {Item.PICKUP}",
        Item.SELLER_CARD,
        Item.SPECIFICS_DL,
        Item.IMAGE_CAROUSEL,
        Item.DESC_IFRAME,
    ),
}


async def _save_unknown(page) -> Path:
    UNKNOWN_DUMP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        html = await page.content()
    except Exception:
        html = ""
    key = hashlib.sha1(f"{page.url}:{len(html)}".encode()).hexdigest()[:12]
    path = UNKNOWN_DUMP_DIR / f"unknown_{key}.html"
    path.write_text(html, encoding="utf-8")
    return path


async def _wait_anchors(page, expect: PageKind, timeout_s: float) -> None:
    """Ждёт появления всех DOM-якорей целевого типа (всё, что будем парсить)."""
    deadline_ms = timeout_s * 1000.0
    loop = asyncio.get_event_loop()
    start = loop.time()
    for sel in READY_ANCHORS.get(expect, ()):
        remaining = deadline_ms - (loop.time() - start) * 1000.0
        if remaining <= 0:
            raise TimeoutError(f"anchor timeout waiting {expect.value} at {page.url}")
        await page.wait_for_selector(sel, state="attached", timeout=remaining)


async def wait_until_ready(
    page,
    expect: PageKind,
    timeout_s: float = PARDON_TIMEOUT_S,
    anchor_timeout_s: float = ANCHOR_TIMEOUT_S,
    ended_selector: str | None = None,
) -> bool:
    """Ждёт, пока ``page`` станет готовой как ``expect``. Возвращает ``True``,
    если страница оказалась завершённым листингом (``ended_selector`` появился
    раньше якорей готовности), иначе ``False``.

    Готовность = (1) не-блок и целевой тип по url+title (page_state), затем
    (2) появление всех DOM-якорей типа (всё, что будем парсить). Бросает
    AccessDeniedError / ErrorPageError / TimeoutError согласно политикам
    из шапки модуля (классификацию «что лечится заменой» делает session).

    ``ended_selector`` (только для item): ENDED-бейдж завершённого листинга.
    Он взаимоисключающ с PRICE_PRIMARY (цены у ended нет), поэтому ждём
    «бейдж ИЛИ первый критический якорь» — что появится первым; бейдж →
    ``True`` (данных листинга нет, дальше не ждём)."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    saved_unknown = False

    while True:
        state = detect_state(page.url, await page.title())

        if state.antibot is Antibot.ACCESS_DENIED:
            raise AccessDeniedError(f"Access Denied at {page.url}")
        if state.antibot is Antibot.ERROR_PAGE:
            raise ErrorPageError(f"Error Page at {page.url}")
        # PARDON — ничего не делаем, продолжаем ждать в цикле.

        if is_ready(state, expect):
            # url целевой и title нормальный — дождёмся DOM-якорей типа.
            await page.wait_for_load_state("domcontentloaded")
            if ended_selector is not None:
                # ended-бейдж ИЛИ цена (взаимоисключающи) — что первым
                await page.wait_for_selector(
                    f"{ended_selector}, {Item.PRICE_PRIMARY}",
                    state="attached", timeout=anchor_timeout_s * 1000.0,
                )
                if await page.query_selector(ended_selector):
                    return True
            await _wait_anchors(page, expect, anchor_timeout_s)
            return False

        if (
            state.antibot is None
            and not saved_unknown
            and state.kind is PageKind.UNKNOWN
        ):
            await _save_unknown(page)
            saved_unknown = True

        if loop.time() >= deadline:
            raise TimeoutError(
                f"ready timeout ({timeout_s}s) waiting {expect.value} at {page.url}"
            )
        await page.wait_for_timeout(int(_POLL_S * 1000))
