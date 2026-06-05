"""Слой 2 — ожидание готовности страницы на живом Playwright `page`.

Воркер навигирует сам (прогрев главной, прокси, стелс — вне библиотеки).
Эта функция крутит цикл ожидания и применяет политики блоков; «что за
страница» и «готова ли» определяет page_state (по url+title, не по DOM).

  PARDON        — ждём, пока пройдёт сам (до timeout_s);
  ERROR_PAGE    — retryable → ErrorPageError (что делать — решает воркер);
  ACCESS_DENIED — жёсткий блок → AccessDeniedError (воркер помирает);
  UNKNOWN url   — не antibot и не целевая: сохраняем HTML для разбора, ждём дальше.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from ..errors import AccessDeniedError, ErrorPageError
from ..parsing.page_state import Antibot, PageKind, detect_state, is_ready
from ..parsing.selectors import Home, Item, Srp

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
        Item.SHIPPING,
        Item.SELLER_CARD,
        Item.SPECIFICS_DL,
        Item.IMAGE_ZOOM,
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
) -> None:
    """Ждёт, пока ``page`` станет готовой как ``expect``.

    Готовность = (1) не-блок и целевой тип по url+title (page_state), затем
    (2) появление всех DOM-якорей типа (всё, что будем парсить). Бросает
    AccessDeniedError / ErrorPageError / TimeoutError согласно политикам.
    """
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
            # url целевой и title нормальный — дождёмся всех DOM-якорей типа.
            await page.wait_for_load_state("domcontentloaded")
            await _wait_anchors(page, expect, anchor_timeout_s)
            return

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
