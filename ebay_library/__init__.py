"""ebay_library — рерайт воркер-библиотеки парсинга eBay (v1 = Mode 1).

Дизайн — SPEC.md, порядок сборки — PLAN.md. Старый пакет ``ebaylib`` не трогаем — это
источник копируемых модулей и DOM-тест-оракул (SPEC.md §12).

Публичный API для оркестратора (он вне библиотеки, SPEC.md §6). Оркестратор отдаёт
колбэки ``next_task``/``task_done`` и (для каталога) ``get_page``, держит ``Store``:

- ``run_item_worker(next_task, task_done, store, *, zip=...)`` — item-лейн (ebaydesc,
  без браузера). Задача: ``{"item_id": "..."}``.
- ``run_catalog_worker(get_page, next_task, task_done, store, *, zip=...)`` — каталог-лейн
  (in-page fetch на прогретой ``get_page``-странице). Задача: батч
  ``{"articles": [...], "condition"?, "min_price"?, "max_price"?}``.
- ``Store`` — клиент записи в ebay_data (asyncpg-пул; владение полями — SPEC.md §2.3).
- Исключения политики ошибок (SPEC.md §7) и модели данных (для типизации у оркестратора).
"""

from .errors import (
    AccessDeniedError,
    ErrorPageError,
    ParseError,
    TransportError,
)
from .models import (
    Catalog,
    CatalogItem,
    CatalogResult,
    ItemEnded,
    ItemPage,
    SearchPage,
    SrpCard,
)
from .store import Store
from .worker import run_catalog_worker, run_item_worker

__all__ = [
    # воркеры (точки входа)
    "run_item_worker",
    "run_catalog_worker",
    # хранилище
    "Store",
    # исключения (политика ошибок, SPEC.md §7)
    "ParseError",
    "TransportError",
    "ErrorPageError",
    "AccessDeniedError",
    # модели данных
    "ItemPage",
    "ItemEnded",
    "SrpCard",
    "CatalogItem",
    "SearchPage",
    "Catalog",
    "CatalogResult",
]
