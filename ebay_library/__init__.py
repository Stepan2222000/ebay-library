"""eBay parsing library — методы парсинга каталога и карточек eBay для воркеров.

Публичное API — здесь; внутренняя раскладка (parsing/ = Слой 1, worker/ =
Слой 2) — деталь реализации, см. specs/architecture.md.
"""

from .config import ITEMS_PER_PAGE, build_search_url
from .download import fetch_images
from .errors import AccessDeniedError, ErrorPageError, ParseError
from .fx import convert_cards
from .parsing.catalog import parse_search_page
from .parsing.item import parse_item_page
from .parsing.models import Catalog, CatalogBatch, CatalogItem, ItemPage, SearchPage, SrpCard
from .parsing.page_state import Antibot, PageKind, classify, detect_antibot, detect_state
from .parsing.zipstate import ship_to_location
from .worker.fetch import fetch_catalog, fetch_catalogs, fetch_item, warmup
from .worker.navigation import wait_until_ready

__version__ = "0.0.1"

__all__ = [
    # построение URL выдачи / errors
    "build_search_url", "ITEMS_PER_PAGE",
    "ParseError", "AccessDeniedError", "ErrorPageError",
    # модели
    "SrpCard", "CatalogItem", "SearchPage", "Catalog", "CatalogBatch", "ItemPage",
    # Слой 1 — чистый парсинг
    "parse_search_page", "parse_item_page",
    "ship_to_location",
    "PageKind", "Antibot", "classify", "detect_antibot", "detect_state",
    # Слой 2 — методы воркера
    "warmup", "fetch_item", "fetch_catalog", "fetch_catalogs",
    "wait_until_ready",
    # конвертация валют в USD (fx-эндпоинт)
    "convert_cards",
    # скачивание фото (HTTP-IO, без браузера)
    "fetch_images",
]
