"""eBay parsing library — методы парсинга каталога и карточек eBay для воркеров.

Публичное API — здесь; внутренняя раскладка (parsing/ = Слой 1, worker/ =
Слой 2) — деталь реализации, см. specs/architecture.md.
"""

from .config import EbayConfig
from .errors import AccessDeniedError, ErrorPageError, ParseError, ZipChangeError
from .parsing.catalog import parse_search_page
from .parsing.item import parse_item_page
from .parsing.models import Catalog, CatalogBatch, CatalogItem, ItemPage, SearchPage
from .parsing.page_state import Antibot, PageKind, classify, detect_antibot, detect_state
from .worker.fetch import fetch_catalog, fetch_catalogs, fetch_item, warmup
from .worker.navigation import wait_until_ready
from .worker.zipcode import current_zip, set_zip

__version__ = "0.0.1"

__all__ = [
    # config / errors
    "EbayConfig",
    "ParseError", "AccessDeniedError", "ErrorPageError", "ZipChangeError",
    # модели
    "CatalogItem", "SearchPage", "Catalog", "CatalogBatch", "ItemPage",
    # Слой 1 — чистый парсинг
    "parse_search_page", "parse_item_page",
    "PageKind", "Antibot", "classify", "detect_antibot", "detect_state",
    # Слой 2 — методы воркера
    "warmup", "fetch_item", "fetch_catalog", "fetch_catalogs",
    "wait_until_ready", "set_zip", "current_zip",
]
