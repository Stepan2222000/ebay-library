# ebay_library

Библиотека воркера для парсинга eBay (Mode 1): товары (PDP) и каталог (SRP), запись
результатов в БД `ebay_data`, готовый цикл «задача → парсинг → запись → подтверждение».
Оркестрация (пул воркеров, очередь задач, переотдача, браузер/прокси) — **вне
библиотеки**: воркер принимает колбэки `next_task` / `task_done` (и для каталога —
`get_page`), всё остальное делает сам.

Два независимых лейна:

- **item** — `run_item_worker`. Тянет товары с `itm.ebaydesc.com` напрямую (`curl_cffi`),
  **без браузера**, JSON-first парсинг встроенной модели страницы.
- **каталог** — `run_catalog_worker`. Запрашивает страницы выдачи `fetch`-ом изнутри
  прогретой браузерной страницы (in-page fetch), парсит DOM (`lxml`), конвертирует
  цены в USD через fx-микросервис.

## Установка

```bash
pip install ebay-library            # имя дистрибутива; импорт — import ebay_library
```

Python 3.11+. Зависимости: `curl_cffi`, `httpx`, `lxml`, `beautifulsoup4`, `asyncpg`.
Для **каталога** нужен Playwright-совместимый браузер (страницы отдаёт ваш `get_page`);
рекомендуется CloakBrowser (`pip install cloakbrowser`) или реальный Chrome
(`channel="chrome"`) — eBay отдаёт им каноническую вёрстку. item-лейну браузер не нужен.

## Публичный API

```python
import ebay_library
# воркеры:    run_item_worker, run_catalog_worker
# хранилище:  Store
# фото:       fetch_photos, Photo, S3Photos, S3Config, fetch_image_urls
# исключения: ParseError, TransportError, ErrorPageError, AccessDeniedError
# модели:     ItemPage, ItemEnded, SrpCard, CatalogItem, SearchPage, Catalog, CatalogResult
```

### Воркеры

```python
async def run_item_worker(next_task, task_done, store, *, zip="19701") -> None
async def run_catalog_worker(get_page, next_task, task_done, store, *, zip="19701") -> None
```

Оба **перманентные**: крутятся, пока не критическая ошибка (исключение наружу) или отмена
корутины. У воркеров есть и параметры конкуренции (`start_c`, `window`, `max_window_s`,
`ceiling`, `idle_s`) — но они **внутренние/предварительные, передавать их НЕ нужно**:
используйте значения по умолчанию (конкуренция адаптивная, подбирается сама).

### Колбэки оркестратора

- **`next_task()`** — `async`, **потокобезопасна** (зовётся параллельно потребителями),
  отдаёт **одну** задачу или `None` (= задач сейчас нет → воркер ждёт, не завершается):

  ```python
  # item:
  {"item_id": "277574984378"}
  # каталог (батч артикулов с общими фильтрами):
  {"articles": ["805079T", "805079"], "condition": "new", "min_price": 50, "max_price": 500}
  ```

- **`task_done(task, stats)`** — `async`, вызывается **строго после записи** в БД
  (`обработана = записана`). `stats` — словарь со статистикой записи и таймингами.
- **`get_page()`** — `async`, **только каталог**: отдаёт свежую прогреваемую браузерную
  страницу (новый контекст/прокси). Зовётся на старте и на каждый swap при Access Denied.

### Минимальный пример — item-лейн (без браузера)

```python
import asyncio
from ebay_library import Store, run_item_worker

async def main():
    store = Store()                          # asyncpg-пул к ebay_data

    async def next_task():
        return await my_queue.take()         # {"item_id": "..."} или None

    async def task_done(task, stats):
        await my_queue.ack(task, stats)       # строго после записи

    await run_item_worker(next_task, task_done, store)   # параметры — по умолчанию

asyncio.run(main())
```

### Минимальный пример — каталог-лейн (с браузером)

```python
import asyncio
from cloakbrowser import launch_async
from ebay_library import Store, run_catalog_worker

async def main():
    browser = await launch_async(headless=False)
    store = Store()

    async def get_page():                    # свежая страница; прокси задаёте контекстом
        ctx = await browser.new_context(proxy={"server": "http://…", "username": "…", "password": "…"})
        return await ctx.new_page()           # прогрев делает сам воркер

    async def next_task():
        return await my_queue.take()          # {"articles": [...], "condition": "new"} или None

    async def task_done(task, stats):
        await my_queue.ack(task, stats)

    await run_catalog_worker(get_page, next_task, task_done, store)   # параметры — по умолчанию

asyncio.run(main())
```

## Запись в ebay_data

`Store` — тонкий asyncpg-клиент серверного API БД: апсерты, диффы, журнал изменений и
death/resurrection делает сама БД (проект `ebay_data`). Можно звать и без воркера:

```python
store = Store(dsn=...)                        # дефолт DSN в коде; env EBAY_DATA_DSN
await store.apply_item(item, zip="19701")     # PDP-снапшот (без цены/доставки — Mode 1)
await store.apply_catalog(article, catalog, zip="19701", condition="new")
await store.close()
```

**Владение полями (важно):** цену в USD и доставку пишет **каталог**; title/condition/
location/seller/specifics/фото/описание — **item**. В Mode 1 item цену/доставку не пишет.

## Фото

Парсер пишет в `item_images` только ссылки (`ebay_url`); скачивание и заливка в S3 —
отдельный шаг (байты в БД не лежат).

```python
async def fetch_photos(item_id, count=None, *, store, upload=False, s3=None) -> list[Photo]
async def fetch_image_urls(item_id) -> list[str]
```

- **`fetch_photos`** — источник URL = БД (`item_images` по `item_id`, первые `count` по
  `idx`). `upload=False` → вернуть байты (`Photo.content`), БД/S3 не трогать. `upload=True`
  → недостающие (без `s3_key`) залить в S3 (`{item_id}/{md5}.jpg`, оригинал JPEG) и
  проставить `s3_key`; в ответе только метаданные (`Photo(idx, ebay_url, url_hash, s3_url,
  uploaded)`). `s3` — переиспользуемый `S3Photos` (дефолт `S3Config` = боевой MinIO, env
  `EBAY_S3_*`); мёртвый листинг при `upload=True` → ошибка. Fail-fast.
- **`fetch_image_urls`** — ссылки на фото **по `item_id`** напрямую с eBay (один GET
  `ebaydesc`, без браузера/БД) — для товаров, которых нет у нас в `item_images`. Дальше при
  желании `fetch_images(urls)` отдаёт байты. ended/404 → `TransportError`.

## Политика ошибок («по жёсткому»)

Наружу летят только **критические** — воркер умирает, незаписанные задачи (без
`task_done`) оркестратор переотдаёт, повторная запись идемпотентна:

- `ParseError` — обязательное поле не выбито (вёрстка/модель уехали; несёт сырьё);
- `TransportError` — item-транспорт исчерпал ретраи или неожиданный статус;
- `ErrorPageError` — «Error Page | eBay»; транспортная смерть браузерной вкладки.

`AccessDeniedError` наружу **не доходит** — это смерть страницы, каталог-сессия сама
берёт новую через `get_page` и продолжает с того же места (Pardon — повторный прогрев).
Транзиент (сеть/`503`/таймаут) — ретраится. Логгер — `"ebay_library"`.

## Документация

Дизайн и контракты — [`../SPEC.md`](../SPEC.md); порядок сборки — [`../PLAN.md`](../PLAN.md);
точные пути JSON-полей товара — [`../examples/README.md`](../examples/README.md).
