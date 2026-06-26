"""Сборка item-лейна (этап 3): один товар → ``ItemPage``. Без пула и без БД (это
этапы 6/5). Оркеструет транспорт (``http/fetch``) и парсинг (``html/item``) в порядке
SPEC.md §4.2: основной запрос → описание (только при успехе основного) → парс.

«По жёсткому» (SPEC.md §7): любой сбой — транспорт (включая сбой запроса описания,
§4.5) или ``ParseError`` — летит наружу и валит задачу. ended/product-hub пока не
классифицируем (добавим позже) — на не-листинге штатно падает ``ParseError``.
"""

from __future__ import annotations

from curl_cffi import AsyncSession

from .html.item import _image_urls_from_html, parse_item_page
from .http.fetch import fetch_description, fetch_item, make_item_session
from .models import ItemPage


async def fetch_item_page(session: AsyncSession, item_id: str, *, prof=None) -> ItemPage:
    """``item_id`` → ``ItemPage`` (Mode 1: без цены/доставки — из каталога, SPEC.md §4.4).

    Порядок (SPEC.md §4.2): основной HTML (``fetch_item``) → описание отдельным
    запросом (``fetch_description``, §4.5) → парс из JSON-модели (§4.3). Сбой любого
    из шагов критичен (по жёсткому) — летит наружу.

    ``prof`` (опц.) — поэтапный профайлер с методом ``switch(name)``: отмечает стадии
    ``fetch`` / ``desc`` / ``parse`` (worker.StageTimer, этап 6)."""
    if prof is not None:
        prof.switch("fetch")
    html = await fetch_item(session, item_id)
    if prof is not None:
        prof.switch("desc")
    description_html = await fetch_description(session, item_id)
    if prof is not None:
        prof.switch("parse")
    return parse_item_page(html, description_html)


async def fetch_image_urls(item_id: str | int) -> list[str]:
    """``item_id`` → ссылки на фото (s-l1600, дедуп; ``ItemPage.image_urls``-набор).

    Лёгкий путь «фото по id» для товаров, которых нет у нас в БД (нет ``ebay_url``):
    ОДИН GET страницы товара (``fetch_item``, без описания) → извлечение только блока
    ``PICTURE`` (``_image_urls_from_html``). Сессию строим внутри на один вызов — снаружи
    нужен только ``item_id``.

    Переиспользует транспорт (``fetch_item``: ретрай ``503``, ``TransportError``) и парс
    (``_image_urls`` — общий с ``parse_item_page``). Исходы: настоящий листинг без фото →
    ``[]``; ended/404/неожиданный статус → ``TransportError`` (наружу, не обрабатываем);
    не листинг/нет модели → ``ParseError``.

    ⚠️ Сессия создаётся НА КАЖДЫЙ вызов — это для ad-hoc «фото по id». Массовый обход
    тысяч id так не гонять (curl_cffi не любит session-per-request) — там путь воркера."""
    session = make_item_session(max_clients=1)
    try:
        html = await fetch_item(session, item_id)
        return _image_urls_from_html(html, str(item_id))
    finally:
        await session.close()
