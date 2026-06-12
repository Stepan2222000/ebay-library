# ebay-library

Библиотека воркера для парсинга eBay: каталог (SRP), страницы товаров (PDP),
фото, запись результатов в БД `ebay_data` и готовый цикл «задача → парсинг →
запись → подтверждение». Оркестрация (пул воркеров, браузер/прокси, очередь
задач) — вне библиотеки: воркер отдаёт три колбека, библиотека делает
остальное.

## Установка

```bash
pip install git+https://github.com/Stepan2222000/ebay-library.git
```

Python 3.11+. Браузер поднимает воркер — любой Playwright-совместимый;
рекомендуется CloakBrowser (`pip install cloakbrowser`) или реальный Chrome
(`channel="chrome"`): eBay отдаёт им каноническую вёрстку.

## Воркер целиком

```python
import asyncio
from cloakbrowser import launch_async
from ebaylib import Store, run_worker

async def main():
    browser = await launch_async(headless=False)
    state = {"ctx": None}

    async def get_page():                 # свежая страница по запросу сессии
        if state["ctx"]:
            await state["ctx"].close()    # старые страницы утилизирует воркер
        state["ctx"] = await browser.new_context()
        return await state["ctx"].new_page()

    async def next_task():                # источник задач: HTTP/Redis/файл…
        return await my_queue.take()      # None → штатное завершение

    async def task_done(task, stats):     # строго ПОСЛЕ записи в БД
        await my_queue.ack(task, stats)

    await run_worker(get_page, next_task, Store(), task_done=task_done)

asyncio.run(main())
```

Формат задач (всё вне `params` — метаданные оркестратора, едут в `task_done`
как есть):

```json
{"type": "catalog", "params": {"articles": ["805079T", "805079"],
                               "zip": "19701", "condition": "new",
                               "min_price": 50, "max_price": 500}}
{"type": "item",    "params": {"item_id": "277574984378", "zip": "19701"}}
```

Контракт жёсткий: **задача = парсинг + запись**; `task_done` вызывается
только после фактической записи (`обработана = записана`). Любая ошибка валит
воркер целиком — незаписанные задачи (без `task_done`) переотдаёт оркестратор,
повторная запись идемпотентна. Блокировки eBay и смерть страницы воркера НЕ
валят: сессия сама берёт новую страницу через `get_page` и продолжает с того
же места.

## Парсинг без БД (EbaySession)

```python
from ebaylib import EbaySession, fetch_images

session = EbaySession(get_page)

res = await session.fetch_catalog(["805079T", "805079"], zip="19701")
res.items                      # все уникальные карточки (CatalogItem, USD)
res.per_query["805079"]        # Catalog по конкретному артикулу

item = await session.fetch_item("277574984378", zip="19701")
photos = await fetch_images(item.image_urls)   # list[bytes], порядок сохранён
```

`zip` обязателен у item (локация сессионная, ставится setter-визитом SRP и
сверяется по `shipToLocation`) и практически обязателен у каталога (без него
доставка не рендерится). Выдача обходится до 5 страниц на запрос
(`max_pages`), цены/доставка конвертируются в USD через fx-микросервис
(`FX_API_URL`) одним батчем на каталог.

## Запись в ebay_data

`Store` — тонкий asyncpg-клиент серверного API БД (`apply_catalog_fetch` /
`apply_item_snapshot`): апсерты, диффы, журнал изменений и death/resurrection
делает сама БД (проект `ebay_data`, `db/schema.sql`). DSN — env
`EBAY_DATA_DSN` (дефолт захардкожен); pgbouncer подключается заменой DSN.
`Store.apply_catalog`/`apply_item` можно звать и без `run_worker`.

## Ошибки

Наружу летят только критические (воркер умирает, задача переотдаётся):
`ParseError` (обязательное поле не выбито; несёт сырой HTML), `ErrorPageError`
(«Error Page | eBay»), `TaskFormatError` (кривая задача), таймауты
(Pardon/якоря/iframe). `AccessDeniedError` наружу не доходит — лечится
заменой страницы внутри. Диагностика — логгер `"ebaylib"` (INFO — замены
страниц с причиной, DEBUG — прогресс).

## Документация

Архитектура, контракты потоков и селекторы — в [`specs/`](specs/):
[architecture](specs/architecture.md) ·
[catalog_flow](specs/catalog_flow.md) ·
[item_flow](specs/item_flow.md) ·
[page_readiness](specs/page_readiness.md)
