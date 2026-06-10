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

    item = await fetch_item(page, "277574984378", zip="19701")
    print(item.title, item.price_usd, item.seller)

    photos = await fetch_images(item.image_urls)         # list[bytes], порядок сохранён
```

Фильтры каталога опциональны (по умолчанию не добавляются), но `zip` на
практике нужен: без него часть карточек рендерится без доставки и парсер
падает. У `fetch_item` `zip` **обязателен**: локация item-страниц сессионная
(URL-параметр на `/itm/` не работает) — библиотека ставит её сама одним
setter-визитом SRP на сессию и сверяет `shipToLocation` на каждом товаре
(см. [item_flow](specs/item_flow.md)).

### Валюта → USD

`fetch_catalog(s)` отдаёт `CatalogItem` с `price` и `shipping_cost` **в USD**.
eBay печатает цены в исходной валюте (`$`, `US $`, `C $`, `EUR`…); после сбора
страниц библиотека одним батчем переводит их в USD через fx-микросервис
(`GET /convert`, см. [parts_prices](../parts_prices)). Адрес сервиса — env
`FX_API_URL` (дефолт `http://194.164.245.107:8092`). Неизвестная валюта или
недоступность сервиса → подзадача падает (без тихих подмен).

Чистый `parse_search_page` (офлайн, на сохранённом HTML) перевод не делает —
отдаёт `SrpCard` с ценой в исходной валюте + токеном `currency_raw`; конвертацию
при необходимости зовут отдельно через `convert_cards(cards)`.

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
