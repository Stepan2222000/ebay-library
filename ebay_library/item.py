"""Сборка item-лейна (этап 3): один товар → ``ItemPage``. Без пула и без БД (это
этапы 6/5). Оркеструет транспорт (``http/fetch``) и парсинг (``html/item``) в порядке
SPEC.md §4.2: основной запрос → описание (только при успехе основного) → парс.

«По жёсткому» (SPEC.md §7): любой сбой — транспорт (включая сбой запроса описания,
§4.5) или ``ParseError`` — летит наружу и валит задачу. ended/product-hub пока не
классифицируем (добавим позже) — на не-листинге штатно падает ``ParseError``.
"""

from __future__ import annotations

from curl_cffi import AsyncSession

from .html.item import parse_item_page
from .http.fetch import fetch_description, fetch_item
from .models import ItemPage


async def fetch_item_page(session: AsyncSession, item_id: str) -> ItemPage:
    """``item_id`` → ``ItemPage`` (Mode 1: без цены/доставки — из каталога, SPEC.md §4.4).

    Порядок (SPEC.md §4.2): основной HTML (``fetch_item``) → описание отдельным
    запросом (``fetch_description``, §4.5) → парс из JSON-модели (§4.3). Сбой любого
    из шагов критичен (по жёсткому) — летит наружу."""
    html = await fetch_item(session, item_id)
    description_html = await fetch_description(session, item_id)
    return parse_item_page(html, description_html)
