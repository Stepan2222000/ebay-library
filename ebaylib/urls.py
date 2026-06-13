"""Построение URL выдачи eBay (Слой 1, без сети и сайд-эффектов).

Единственный источник URL поиска: артикул + страница + опциональные фильтры
(ZIP доставки, состояние, цена). Воркер импортирует ``build_search_url`` и сам
решает значения фильтров — дефолтов нет; рекомендованные значения — в README.
"""

from __future__ import annotations

import urllib.parse

# Карточек на страницу выдачи. Один источник: и для URL (`_ipg`), и для условия
# конца пагинации (полная страница = ровно столько карточек).
ITEMS_PER_PAGE = 240

_SEARCH_BASE = "https://www.ebay.com/sch/i.html"

# Состояние товара → код eBay `LH_ItemCondition`. "all" → фильтр не добавляем
# (любое состояние). "new" = Brand New + New Other; "used" = Used / Pre-Owned.
_CONDITION_CODES = {"new": 3, "used": 3000}


def _fmt_price(value: float) -> str:
    """Цена в URL: целое без дробной части (50 → '50'), иначе как есть."""
    f = float(value)
    return str(int(f)) if f.is_integer() else str(value)


def build_search_url(
    query: str,
    *,
    page: int = 1,
    zip: str | None = None,
    condition: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
) -> str:
    """URL страницы выдачи eBay для поискового запроса (артикула).

    Структурные части всегда: ``_nkw`` (запрос, URL-энкодится), ``_ipg=240``,
    ``_pgn`` (страница, 1-based), ``_dmd=1`` (режим отображения = list-view).
    ``_dmd=1`` обязателен: eBay A/B показывает SRP в list-view (список, у
    карточек есть строка продавца "<ник> NN% positive") ИЛИ grid-view (сетка,
    продавца НЕТ ни в DOM, ни в embedded-JSON). В grid-view парсер падает на
    обязательном seller — ``_dmd=1`` форсит list-view (подтверждено live
    2026-06-13: 0914930/65606/854553 — сетка без продавца → список с продавцом).

    Опциональные фильтры — только если заданы:

    - ``zip`` → ``_stpos``: индекс доставки. Без него часть карточек рендерится
      без доставки ("Shipping not specified") и парсер падает — на практике
      задавайте всегда (рекоменд. "19701", см. README).
    - ``condition`` → ``LH_ItemCondition``: ``"all"`` (или ``None``) — без
      фильтра; ``"new"`` = New (Brand New + New Other); ``"used"`` = Used /
      Pre-Owned. Другое значение → ``ValueError``.
    - ``min_price`` → ``_udlo``, ``max_price`` → ``_udhi``: границы цены (USD).
    """
    params: list[tuple[str, object]] = [
        ("_nkw", query),
        ("_sacat", "0"),
        ("_from", "R40"),
        ("rt", "nc"),
        ("_ipg", ITEMS_PER_PAGE),
        ("_pgn", page),
        ("_dmd", 1),  # list-view: у карточек есть продавец (grid-view его не рендерит)
    ]
    if condition is not None and condition != "all":
        try:
            params.append(("LH_ItemCondition", _CONDITION_CODES[condition]))
        except KeyError:
            raise ValueError(
                f"condition must be 'all', 'new' or 'used', got {condition!r}"
            ) from None
    if zip is not None:
        params.append(("_stpos", zip))
    if min_price is not None:
        params.append(("_udlo", _fmt_price(min_price)))
    if max_price is not None:
        params.append(("_udhi", _fmt_price(max_price)))
    return _SEARCH_BASE + "?" + urllib.parse.urlencode(params)
