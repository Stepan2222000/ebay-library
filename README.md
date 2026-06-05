# ebay-library

Библиотека методов парсинга eBay для воркеров: каталог (SRP), страницы товаров
(PDP), смена ZIP, ожидание готовности/антибота. Оркестрация (пул воркеров,
прокси, раздача задач) — вне библиотеки: воркер передаёт готовый Playwright
`page`, библиотека делает остальное.

## Установка

```bash
pip install git+https://github.com/Stepan2222000/ebay-library.git
playwright install chromium
```

Python 3.11+. Для live-работы UI (смена ZIP) нужен установленный Google Chrome —
запускать с `channel="chrome"`.

## Использование

```python
from playwright.async_api import async_playwright
from ebay_library import warmup, fetch_catalogs, fetch_item

async with async_playwright() as p:
    browser = await p.chromium.launch(headless=False, channel="chrome")
    page = await (await browser.new_context(locale="en-US")).new_page()

    await warmup(page)                                   # один раз на сессию

    batch = await fetch_catalogs(page, ["8M0142836", "861787"])
    print(len(batch.items), "карточек,", len(batch.errors), "ошибок")

    item = await fetch_item(page, "277574984378")
    print(item.title, item.price_usd, item.seller)
```

Чистые парсеры (`parse_search_page`, `parse_item_page`) работают на
сохранённом HTML без браузера.

## Документация

Архитектура, контракты потоков и селекторы — в [`specs/`](specs/):
[architecture](specs/architecture.md) ·
[catalog_flow](specs/catalog_flow.md) ·
[item_flow](specs/item_flow.md) ·
[page_readiness](specs/page_readiness.md)
