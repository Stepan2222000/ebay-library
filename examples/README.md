# Примеры реальных данных + как их получать

Эта папка — **конкретные примеры реально полученных данных** и **точная процедура их
получения** (код-уровень). Спека (`../SPEC.md`) описывает логику; здесь — живые
образцы и как именно достать каждый тип данных.

---

## 1. Страница товара → JSON-модель `modules`

**Пример:** [`item_modules_sample.json`](item_modules_sample.json) — реальный объект
`modules` товара `277574984378` (60 модулей).

**Как получить:**
1. `GET https://itm.ebaydesc.com/itm/{item_id}` (un-walled, без антибота/браузера) —
   приходит полный HTML страницы товара.
2. В HTML найти маркер `"modules":{` и **извлечь сбалансированный объект** от первой
   `{` до парной `}` (с учётом строк и экранирования). Выбрать **самый крупный** такой
   объект (он — основная модель). Распарсить как JSON.

```python
import re, json
def balanced(s, start):           # сбалансированный {...} от позиции start
    d=0; ins=False; esc=False
    for i in range(start, len(s)):
        c=s[i]
        if ins:
            if esc: esc=False
            elif c=='\\': esc=True
            elif c=='"': ins=False
        elif c=='"': ins=True
        elif c=='{': d+=1
        elif c=='}':
            d-=1
            if d==0: return s[start:i+1]

def get_modules(html):
    best=None
    for m in re.finditer(r'"modules"\s*:\s*\{', html):
        obj=balanced(html, html.index("{", m.start()+9))
        if obj and (best is None or len(obj)>len(best)): best=obj
    return json.loads(best)
```

**Где какие поля в `modules`** (текстовые значения — в деревьях `TextualDisplay →
textSpans[] → {"_type":"TextSpan","text":...}`, собираются рекурсивно). Покрытие — по
прогону 1000 разных запчастей (995 живых PDP, БД как истина):

| поле | путь | покрытие |
|---|---|---|
| title | `JSONLD.product.name` (HTML-кодирован → чистим `bs4.get_text`, см. ниже) | 994/994 |
| condition | `JSONLD.product.offers.itemCondition` → хвост URL (`NewCondition`→`new`, прочее→`other`) | 995/995 |
| item_number | `ITEM_SPEC_SUMMARY.sections.itemId.dataItems.itemId.textSpans[1]` (спан-индекс 1, цифры) | 994/994 |
| last_updated | `ITEM_SPEC_SUMMARY.sections.revisionHistory` (опционально) | — |
| specifics | `ABOUT_THIS_ITEM.sections.features.dataItems` → `{labels[0] → значения}` (см. ниже) | 994/994 |
| location | `SHIPPING_ATF_SECTION_MODULE.sections.shipping.dataItems` → спан с префиксом «Located in:» | 994/994 |
| images | `PICTURE.mediaList[].image.originalImg.URL` (→ `s-l1600`, дедуп) | 993/994 |
| price | `JSONLD.product.offers.price` + `priceCurrency` | 994/994 |

Важно (уточнено на сверке с боевой БД, этап 1):
- **title HTML-кодирован** (`&#034;`, `&amp;`, тег `<wbr/>`) — чистим через
  `BeautifulSoup(name,"html.parser").get_text()` (HTML регексом не парсим). textSpans
  модулей — уже чистый текст, их не трогаем.
- **specifics**: значение = `textSpans` без спанов-ссылок (у них есть `action` — это UI,
  напр. «See all condition definitions»). Строку с внутренним ключом **`condition`**
  (boilerplate-определение состояния, дубль поля `condition`) **исключаем**.
- **images — только `PICTURE.mediaList`, НЕ `JSONLD.image`**: `JSONLD.image` обрезан до
  **5** фото, а `mediaList` несёт полную галерею (расходятся в ~490/993). Пустой
  `mediaList` → у товара реально нет фото (`[]` валидно).
- **price — в валюте гео запроса, НЕ USD** (с не-US egress; на прогоне с EU-edge —
  `priceCurrency: "CZK"` на всех 994). В Mode 1 цену из товара не берут (из каталога,
  в USD) — см. `../SPEC.md` §4.4.

Поле **вне `modules`** (но в том же HTML): **seller** — regex `"sellerUserName":"…"`
по полному HTML. Это **username** (как в каталоге), проверено **997/997** совпадений с
БД. Слаги `_ssn=`/`/str/` ненадёжны (≠ username). Внутри `modules` `sellerUserName`
может отсутствовать — искать по всему HTML.

Не-листинговые страницы (≈6/1000): транзиент-`503` (ретрай), `404`, и **product-hub**
(`<title>… for sale online | eBay`, нет `sellerUserName`; пример `397974415442`) —
см. `../SPEC.md` §11.

---

## 2. Описание товара

**Пример:** [`item_description_sample.html`](item_description_sample.html) — сырое
описание товара `277574984378`.

**Как получить:** `GET https://itm.ebaydesc.com/itmdesc/{item_id}` — отдельный
документ только с описанием (в основной странице описания нет). Чистим теги
(script/style) → текст. Пустое описание — валидно.

---

## 3. Каталог (выдача SRP) → карточки

**Пример:** [`../catalog_dump.json`](../catalog_dump.json) — распарсенные карточки по
15 артикулам (item_id, condition, price, валюта, **доставка**, seller, location,
title, флаг фото).

**Как получить:**
1. В **открытой браузерной странице** на `www.ebay.com` выполнить `fetch` URL выдачи
   изнутри страницы (in-page fetch, same-origin, `credentials:'include'` — несёт куки
   сессии):
   `https://www.ebay.com/sch/i.html?_nkw={артикул}&_ipg=240&_pgn=1&_dmd=1&_stpos=19701`
   (+ фильтры). Возвращается сырой HTML выдачи.
2. Распарсить **из DOM** (в выдаче цельной JSON-модели по карточкам нет): карточки
   `li.s-card[data-listingid]`. В образце — результат текущего `parse_search_page`.

Доставка в карточке **корректная** (за счёт `_stpos=19701` в URL).
