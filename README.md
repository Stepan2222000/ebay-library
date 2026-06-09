# ebay-library

Библиотека методов парсинга eBay для воркеров: каталог (SRP), страницы товаров
(PDP), скачивание фото, ожидание готовности/антибота. Оркестрация (пул воркеров,
прокси, раздача задач) — вне библиотеки: воркер передаёт готовый Playwright
`page`, библиотека делает остальное.

## Установка

```bash
pip install git+https://github.com/Stepan2222000/ebay-library.git
playwright install chromium
```

Python 3.11+. Рекомендуется реальный Google Chrome (`channel="chrome"`) —
eBay отдаёт ему стабильную каноническую вёрстку SRP.

## Использование

ZIP доставки, состояние и цена задаются параметрами (через URL) — отдельной
UI-установки ZIP нет. Рекомендованные значения: `zip="19701"`, `condition="new"`.

```python
from playwright.async_api import async_playwright
from ebay_library import warmup, fetch_catalogs, fetch_item, fetch_images

async with async_playwright() as p:
    browser = await p.chromium.launch(headless=False, channel="chrome")
    page = await (await browser.new_context(locale="en-US")).new_page()

    await warmup(page)                                   # один раз на сессию

    # condition: "all" (без фильтра) | "new" (Brand New + New Other) | "used"
    batch = await fetch_catalogs(
        page, ["8M0142836", "861787"],
        zip="19701", condition="new", min_price=50, max_price=500,
    )
    print(len(batch.items), "карточек,", len(batch.errors), "ошибок")

    item = await fetch_item(page, "277574984378")
    print(item.title, item.price_usd, item.seller)

    photos = await fetch_images(item.image_urls)         # list[bytes], порядок сохранён
```

Фильтры опциональны (по умолчанию не добавляются), но `zip` на практике нужен:
без него часть карточек рендерится без доставки и парсер падает.

### URL выдачи отдельно

`build_search_url` импортируется и строит URL сам (если нужен прямой контроль):

```python
from ebay_library import build_search_url

build_search_url("8M0142836", page=1, zip="19701", condition="new")
# https://www.ebay.com/sch/i.html?_nkw=8M0142836&_sacat=0&_from=R40&rt=nc&_ipg=240&_pgn=1&LH_ItemCondition=3&_stpos=19701
```

Чистые парсеры (`parse_search_page`, `parse_item_page`) работают на
сохранённом HTML без браузера.

## Документация

Архитектура, контракты потоков и селекторы — в [`specs/`](specs/):
[architecture](specs/architecture.md) ·
[catalog_flow](specs/catalog_flow.md) ·
[item_flow](specs/item_flow.md) ·
[page_readiness](specs/page_readiness.md)
