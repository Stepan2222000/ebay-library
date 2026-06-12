"""eBay parsing library — парсинг каталога/товаров eBay и запись в ebay_data.

Публичное API — только необходимое воркеру:

- ``run_worker`` + ``Store`` — цикл «задача → парсинг → запись → task_done»;
- ``EbaySession`` — парсинг без БД-цикла (каталог/товар на живом Playwright);
- ``fetch_images`` — скачивание фото (HTTP, без браузера);
- модели результатов и типы исключений (по ним оркестратор различает смерти).

Внутренняя раскладка — деталь реализации (см. specs/architecture.md);
внутренности доступны по полным путям (``ebaylib.html.*``, ``ebaylib.urls``,
``ebaylib.http.*``, ``ebaylib.browser.*``), но публичным контрактом не являются.
"""

from .browser.session import EbaySession
from .errors import AccessDeniedError, ErrorPageError, ParseError
from .http.images import fetch_images
from .models import Catalog, CatalogItem, CatalogResult, ItemPage
from .store import Store
from .worker import TaskFormatError, run_worker

__version__ = "0.1.0"

__all__ = [
    # точка входа воркера
    "run_worker", "Store", "EbaySession",
    # фото (отдельная операция, без браузера)
    "fetch_images",
    # результаты
    "CatalogResult", "Catalog", "CatalogItem", "ItemPage",
    # исключения — типы смерти воркера
    "ParseError", "AccessDeniedError", "ErrorPageError", "TaskFormatError",
]
