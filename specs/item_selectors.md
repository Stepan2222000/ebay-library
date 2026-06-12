# eBay — селекторы страницы товара (item / PDP)

Исходно основано на сейвах cloak_runner (вне этого репо); актуализировано и
подтверждено live на en-US — фикстуры в `tests/fixtures/` (item_*.html,
US + intl). Боевые селекторы — `ebay_library/parsing/selectors.py`; контракт потока
(два HTML, правила полей) — [item_flow.md](item_flow.md).

---

## Название (title)

```
[data-ebay-critical-module="TITLE"]  h1.x-item-title__mainTitle  span.ux-textspans
```

Текст внутри `<span class="ux-textspans">`. Работает на всех проверенных
фикстурах (US + intl) — фолбек не нужен.

---

## eBay item number

Живёт в таб-хедере `[data-testid="d-item-details-tab-header"]` (соседствует
с критическими модулями, но сам критическим не помечен).

```
[data-testid="d-item-details-tab-header"]
  .ux-layout-section__textual-display--itemId  span.ux-textspans--BOLD
```

Возвращает строго число (например, `116876190531`).

---

## Last updated

В том же таб-хедере, что и item number. Строка содержит span с
`Last updated on` и рядом — дату вида `Dec 12, 2025 18:24:32 PST`.

Селектор row:

```
[data-testid="d-item-details-tab-header"]
  .ux-layout-section--revisionHistory  .ux-layout-section__row
```

Внутри — два `.ux-textspans`:
- `<span class="ux-textspans ux-textspans--HIGHLIGHT">Last updated on</span>`
- `<span class="ux-textspans">Dec 12, 2025 18:24:32 PST</span>` (рядом)

Достаём текст всего row, отрезаем префикс `Last updated on` — остаётся дата.

---

## Имя продавца

⚠️ Видимый текст (`.x-sellercard-atf__info__about-seller a span.ux-textspans--BOLD`)
— это **display-name** («Florida Tool Shed»), витрина, может меняться.
Сохраняем **username** (как в каталоге, общий ключ).

**Единый источник — embedded-JSON страницы: `"sellerUserName":"<ник>"`**
(regex по сырому HTML, ровно одно вхождение и всегда владелец листинга;
проверено live 2026-06-12 на 23 PDP: с магазином/без/EU-сессии).

Ссылки карточки продавца (`.x-sellercard-atf a[href]`) для извлечения ника
НЕ используем — устаревший способ, у него три провала (всё live):

- у продавцов БЕЗ магазина ни `_ssn=`, ни `/str/` нет вовсе — ссылки вида
  `/sch/<username>/m.html` (напр. 236598927330, `tomt7600`);
- слаг `/str/<slug>` может отличаться от username
  (`avantims` ≠ `avantimotorsports`, item 376748192596);
- `/str/` матчит и ЧУЖИЕ магазины из рекламных блоков (`/str/gpscity` на
  странице другого продавца).

Подтверждено live: `fltoolbox` ↔ «Florida Tool Shed», `bootundmotor` ↔
«Boot und Motor». Контракт — [item_flow.md](item_flow.md).

---

## Текущая цена

**Правило: всегда сохраняем цену в USD.** На intl-листингах (AU/CAD/EUR/GBP)
eBay сам показывает `Approximately US $X.XX` рядом с native-ценой — берём
эту строку. На US-листингах конвертации нет, берём primary.

### Native price (BOLD, крупная)

```
.x-price-primary > span.ux-textspans
```

⚠️ **Только прямой child** (`>`). Внутри `.x-price-primary` есть ещё
`span.x-price-primary__orBestOffer` с вложенным
`<span class="ux-textspans ux-textspans--SECONDARY">or Best Offer</span>`.
Селектор с пробелом (`.x-price-primary span.ux-textspans`) ловит обоих.

Реальные значения: `US $129.00`, `AU $110.00`, `C $342.50`, `EUR 691.25`.

### USD approximation (Approximately US $X.XX) — для intl-листингов

```
.x-price-approx .x-price-approx__price
```

Возвращает строго `US $X.XX`. На US-листингах этот блок отсутствует
(0 совпадений) — это и есть индикатор «native уже USD».

Подтверждено live на https://www.ebay.com/itm/116709108878:
- `.x-price-primary > span.ux-textspans` → `AU $110.00`
- `.x-price-approx .x-price-approx__price` → `US $78.85`

### Логика выбора финальной цены

  - если `.x-price-approx .x-price-approx__price` есть → `price_usd` =
    распарсенное число оттуда, `price_native` + `currency_native` = из
    `.x-price-primary`
  - иначе (USD-листинг) → `price_usd` = распарсенное из
    `.x-price-primary`, `price_native` = `None`

В выгрузку всегда идёт `price_usd` как основная цена.

### Суффикс `/ea`, `/lot`, `/100ct`

На quantity-листингах цена приходит как `US $88.35/ea` (видели также
`US $59.00/ea`, `US $72.00/ea`). Регэксп цены матчит число до суффикса,
суффикс **отбрасываем** — в `price_usd` кладём только число (`88.35`).
Поле «за единицу» отдельно не сохраняем.

Примеры: https://www.ebay.com/itm/146084555961 (`US $88.35/ea`),
https://www.ebay.com/itm/198292601960 (`US $59.00/ea`).

---

## Состояние

Якорим через `[data-testid="x-item-condition"]` (блок в правой ATF-колонке
buybox-зоны, критическим модулем не помечен).

```
[data-testid="x-item-condition"]  .x-item-condition-text  .ux-textspans
```

Текст вида `New`, `New (Other)`, `New other (see details)`, `Open box`,
`Pre-owned`, …

---

## Стоимость доставки

Финализировано после live-прогона ~100 разных item-страниц с выставленным
ZIP=19701.

### Корневой селектор

```
[data-ebay-critical-module="SHIPPING_ATF_SECTION_MODULE"]
  .ux-labels-values--shipping
  .ux-labels-values__values-content > div:first-child
```

Берём `innerText` этого `<div>` и применяем правила в порядке (первый
матч побеждает). Структура span'ов внутри **не одинакова**, поэтому
надёжнее парсить именно `innerText`, а не цеплять отдельные span'ы.

### Извлечение (итог в USD — контракт в [item_flow.md](item_flow.md))

Сохраняем только итоговую стоимость в USD (method/carrier и native-валюту
не выделяем): `Free…` → 0.0; intl → число из `(approx US $X)`; US → `US $X`
в начале строки; не выбили → ошибка.

Реальные форматы innerText (live, ZIP=19701, ~100 страниц):

| формат | пример |
|---|---|
| free | `Free 2-4 day delivery`, `Free Expedited Shipping from outside US` |
| us paid | `US $10.95 USPS Ground Advantage®. See details for shipping` |
| us paid express | `US $7.04 delivery in 2–4 days` (без See details) |
| intl + approx | `AU $150.00 (approx US $107.52) Standard International Flat Rate Postage. See details` |
| intl EUR (без $) | `EUR 29.99 (approx US $34.96) Standard International. See details` |
| freight | `Freight - Check the item description or Contact the seller for details` (цены нет) |
| no price | `Will ship to United States. Read item description or contact seller for shipping options…` — продавец не настроил доставку до ZIP (live 174601590686) |

Тексты с «contact seller» без суммы → `shipping_cost = None` (опционально,
контракт в [item_flow.md](item_flow.md)); прочие непарсящиеся → ошибка.

⚠️ Хвост `eBay International Shipping Shop with confidence… Learn more . See
details` — маркетинговый мусор в том же innerText; стоимость всегда в начале
строки (или в `(approx US $X)`), поэтому на извлечение USD не влияет.

### Локация продавца

⚠️ В intl-кейсах span `ux-textspans--SECONDARY` стоит и на конвертации
`(approx US $Y.YY)` — без текстового фильтра берётся конвертация вместо
локации. Правильно: пройти по всем `span.ux-textspans--SECONDARY` (по всему
документу — контейнер бывает `--shipping` или `--legalShipping`, см.
[item_flow.md](item_flow.md)) и взять первый с текстом, начинающимся с
`Located in:`. Реальные значения live: `Tacoma, Washington, United States`,
`Shenzhen, China`, `Campbellfield, VIC, Australia`, `NORRTÄLJE, Sweden`.

---

## Item specifics (таблица характеристик)

Контейнер:

```
[data-ebay-critical-module="ABOUT_THIS_ITEM"]                   ИЛИ
[data-testid="ux-layout-section-evo"].ux-layout-section--features
```

Каждая пара ключ-значение:

```
dl.ux-labels-values   (data-testid="ux-labels-values")
```

На каждой `<dl>` есть модификатор класса вида `ux-labels-values--brand`,
`ux-labels-values--mpn`, `ux-labels-values--countryOfOrigin` и т.д. — можно
использовать для адресного парсинга конкретных полей.

- Ключ: `dl dt .ux-textspans`
- Значение: `dl dd .ux-textspans`

Пример строки:

```html
<dl data-testid="ux-labels-values" class="ux-labels-values ux-labels-values--brand">
  <dt><div><span class="ux-textspans">Brand</span></div></dt>
  <dd><div><span class="ux-textspans">MerCruiser</span></div></dd>
</dl>
```

---

## Описание товара (description)

```
iframe#desc_ifr
```

Содержимое лежит **в iframe** (`itm.ebaydesc.com`) — отдельный HTML-документ,
в основном HTML страницы его текста нет. Как добываем (frame.content() после
догрузки фрейма) и контракт двух HTML — [item_flow.md](item_flow.md).

---

## Галерея фото

Все картинки товара:

```
.ux-image-carousel-item  img      ← IMAGE_CAROUSEL
```

**Большая версия — строится из URL картинки**: берём `src` (у ленивых
картинок дальних слайдов `src` нет — URL лежит в `data-src`) и заменяем токен
размера на `s-l1600` (`…/g/<id>/s-l500.webp` → `…/g/<id>/s-l1600.webp`).
CDN всегда отдаёт максимум существующего размера (клампит к оригиналу, без
404 — проверено httpx: для 500px-оригинала s-l1600/s-l2000 возвращают тот же
файл).

⚠️ `data-zoom-src` (раньше брали его) у части листингов **пустой** — когда
большой версии нет физически (оригинал ≤500px; live 125943066590) — поэтому
от него не зависим. Дедуп по URL с сохранением порядка карусели (eBay
дублирует каждое фото в двух img).

---

## Message seller (написать продавцу)

⚠️ Кнопка `<button class="ux-call-to-action btn btn--secondary" data-testid="ux-call-to-action">`
встречается на странице **трижды** с одинаковыми классами:
- `Add to Watchlist` / `Added to Watchlist` (в buybox-зоне)
- `Message seller` №1 — в верхнем `vim x-store-information` (buybox, мини-карточка продавца)
- `Message seller` №2 — в нижнем `vim x-store-information` (About this seller)

Подтверждено на 3 разных item HTML (815822A30, 8M0078858, 46-42089A5): на
каждом — 3 такие кнопки с разным текстом. Watchlist лежит **вне**
`x-store-information`. Поэтому селектор
`.x-store-information button.ux-call-to-action.btn--secondary`
матчит **2** кнопки (обе Message seller) — буквально подтверждено в DevTools
(см. скриншот пользователя: `1 of 2`).

Чистого CSS, отличающего «buybox Message seller» от «About-this-seller
Message seller», нет — оба контейнера называются одинаково. Решение —

**Через Playwright text-locator** (рекомендую):

```python
page.get_by_role("button", name="Message seller").first
```

**Через контейнер** (исключает Watchlist, но всё равно даёт 2):

```
.x-store-information button.ux-call-to-action.btn--secondary
```

Обе матчатся (buybox + About-this-seller). Берём `.first` — обе ведут в
одну и ту же диалоговую форму.

**Чистым CSS отделить от Watchlist** одной кнопкой нельзя (одинаковые
классы, оба внутри `vim x-store-information` — нет — Watchlist вне, но
text-фильтра в CSS без `:has()` не сделать). Использовать Playwright
`has_text=` или `get_by_role`.
