"""ebay_library — рерайт воркер-библиотеки парсинга eBay (v1 = Mode 1).

Дизайн — SPEC.md, порядок сборки — PLAN.md. Старый пакет ``ebaylib`` не трогаем — это
источник копируемых модулей и DOM-тест-оракул (SPEC.md §12).

Публичный API для оркестраторов (SPEC.md §6):
- ``run_item_worker(next_task, task_done, store, *, zip=...)`` — item-лейн (готов);
- ``Store`` — клиент записи в ebay_data (на пуле);
- ``run_catalog_worker`` — добавится с каталог-лейном (PLAN этап 9).
"""

from .store import Store
from .worker import run_item_worker

__all__ = ["Store", "run_item_worker"]
