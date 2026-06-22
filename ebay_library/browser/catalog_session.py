"""Слой 2 — каталог-сессия (этап 7): выдача через **in-page fetch** на живой
браузерной странице (SPEC.md §5.1–5.3).

Идея: держим одну открытую (прогретую) страницу на ``www.ebay.com`` — это живая
доверенная антибот-сессия. Страницы выдачи (SRP) запрашиваем не навигацией, а
``fetch``-ом ИЗНУТРИ страницы (same-origin, ``credentials:'include'`` — несёт куки
сессии). Возвращается **полный серверный HTML без рендера**: DOM-якорей не ждём,
тело классифицируем через ``page_state.detect_state_html`` (по ``response.url`` +
``<title>``), затем парсим ``parse_search_page`` и переводим в USD (fx, §5.4).

Этап 7 — **последовательно один артикул** (параллельность/адаптив — этап 9), **без
контура восстановления** (Pardon/Access Denied — этап 8): здесь не-SRP исход просто
летит наружу (``PardonError`` / ``AccessDeniedError`` / ``ErrorPageError``).

⚠️  Браузерная часть проверяется ЖИВЬЁМ только на сервере (cloakbrowser+Xvfb через
US-прокси) — с Mac не гоняется. Контракт ``page`` — Playwright-совместимая страница
от оркестратора (``get_page``), уже на ``www.ebay.com`` и прогретая ``warmup``.
"""

from __future__ import annotations

import logging

from ..errors import AccessDeniedError, ErrorPageError, PardonError
from ..html.page_state import Antibot, PageKind, detect_state_html
from ..html.srp import parse_search_page
from ..http.fx import convert_cards
from ..models import Catalog, CatalogItem, CatalogResult, SrpCard
from ..urls import ITEMS_PER_PAGE, build_search_url

logger = logging.getLogger("ebay_library")

_HOME_URL = "https://www.ebay.com/"
MAX_PAGES = 5

# JS: same-origin fetch с куками. Возвращаем финальный URL (после редиректов —
# Pardon уводит на /splashui/challenge), статус и полное тело. p50-латентность —
# performance.now() внутри (пригодится адаптиву на этапе 9).
_FETCH_JS = """
async (url) => {
  const t0 = performance.now();
  const r = await fetch(url, { credentials: 'include' });
  const body = await r.text();
  return { url: r.url, status: r.status, body, ms: performance.now() - t0 };
}
"""


async def warmup(page) -> None:
    """Прогрев: заход на главную eBay (живая антибот-сессия). Без него первый SRP
    ловит антифрод (SPEC.md §5.1)."""
    await page.goto(_HOME_URL, wait_until="domcontentloaded")
    logger.debug("catalog warmup ok: %s", page.url)


async def in_page_fetch(page, url: str) -> dict:
    """``fetch`` URL изнутри страницы (same-origin, с куками). → {url, status, body, ms}."""
    return await page.evaluate(_FETCH_JS, url)


async def _fetch_srp_html(page, url: str) -> str:
    """In-page fetch SRP + классификация тела. Возвращает HTML, если это готовая SRP;
    иначе летит наружу (этап 7 без восстановления, контур — этап 8)."""
    res = await in_page_fetch(page, url)
    state = detect_state_html(res["url"], res["body"])
    if state.antibot is Antibot.ACCESS_DENIED:
        raise AccessDeniedError(f"Access Denied at {res['url']}")
    if state.antibot is Antibot.ERROR_PAGE:
        raise ErrorPageError(f"Error Page at {res['url']}")
    if state.antibot is Antibot.PARDON or state.kind is PageKind.PARDON:
        raise PardonError(f"Pardon at {res['url']}")
    if state.kind is not PageKind.SRP:
        raise ErrorPageError(f"unexpected page kind {state.kind.value} at {res['url']}")
    return res["body"]


async def fetch_catalog(
    page,
    queries: str | list[str],
    *,
    zip: str | None = None,
    condition: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_pages: int = MAX_PAGES,
) -> CatalogResult:
    """Каталоги по запросу или списку запросов через in-page fetch (один за другим).

    По каждому запросу листаем страницы выдачи (стоп: сепаратор fewer-words, неполная
    страница либо лимит ``max_pages``; нахлёст соседних страниц гасится дедупом по
    item_id), затем один fx-батч переводит цены/доставку в USD (SPEC.md §5.4).
    Дубликаты запросов отбрасываются. ``zip`` на практике нужен всегда (рекоменд.
    "19701") — иначе доставка не рендерится и парсер падает."""
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
            html = await _fetch_srp_html(page, url)
            sp = parse_search_page(html)
            pages_fetched += 1
            logger.debug("srp %r pgn=%d: %d cards", query, pgn, len(sp.items))
            if pgn == 1:
                results_count = sp.results_count
            has_sep = has_sep or sp.has_fewer_words_sep
            for c in sp.items:
                if c.item_id not in qseen:
                    qseen.add(c.item_id)
                    cards.append(c)
            # Стоп: «похожие» (fewer-words), неполная страница (последняя) либо кап.
            if sp.has_fewer_words_sep or len(sp.items) < ITEMS_PER_PAGE:
                break
            if pgn >= max_pages:
                break
            pgn += 1

        converted = await convert_cards(cards)        # → USD (SPEC.md §5.4)
        per_query[query] = Catalog(
            query=query, results_count=results_count, items=converted,
            pages_fetched=pages_fetched, has_fewer_words_sep=has_sep,
        )
        for it in converted:
            if it.item_id not in seen:
                seen.add(it.item_id)
                items.append(it)

    return CatalogResult(items=items, per_query=per_query)
