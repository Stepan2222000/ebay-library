"""eBay parsing library — методы парсинга каталога и карточек eBay для воркеров.

Публичное API — здесь; внутренняя раскладка — деталь реализации
(см. specs/architecture.md):

- ``html/``    — Слой 1: чистый парсинг HTML/url → данные (без браузера и сети);
- ``http/``    — HTTP-IO без браузера (fx-конвертация, фото);
- ``browser/`` — Слой 2: живой Playwright (готовность, ``EbaySession``).
"""

from .errors import AccessDeniedError, ErrorPageError, ParseError
from .html.item import parse_item_page, ship_to_location
from .html.page_state import Antibot, PageKind, classify, detect_antibot, detect_state
from .html.srp import parse_search_page
from .http.fx import convert_cards
from .http.images import fetch_images
from .models import Catalog, CatalogItem, CatalogResult, ItemPage, SearchPage, SrpCard
from .urls import ITEMS_PER_PAGE, build_search_url

__version__ = "0.1.0"

__all__ = [
    # построение URL выдачи / errors
    "build_search_url", "ITEMS_PER_PAGE",
    "ParseError", "AccessDeniedError", "ErrorPageError",
    # модели
    "SrpCard", "CatalogItem", "SearchPage", "Catalog", "CatalogResult", "ItemPage",
    # Слой 1 — чистый парсинг
    "parse_search_page", "parse_item_page",
    "ship_to_location",
    "PageKind", "Antibot", "classify", "detect_antibot", "detect_state",
    # конвертация валют в USD (fx-эндпоинт)
    "convert_cards",
    # скачивание фото (HTTP-IO, без браузера)
    "fetch_images",
]
