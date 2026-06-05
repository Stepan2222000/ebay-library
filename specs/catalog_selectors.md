# eBay — селекторы каталога (SRP)

Исходно основано на pt-BR сейвах (cloak_runner, вне этого репо); актуализировано
и подтверждено live на en-US — фикстуры в `tests/fixtures/` (srp_*.html).
Боевые селекторы — единственной точкой в `ebay_library/parsing/selectors.py`; контракт
потока (URL, ZIP, пагинация, batch) — [catalog_flow.md](catalog_flow.md).

---

## Карточка товара в выдаче

### Контейнер

```
li.s-card
```

Первая карточка на странице — placeholder `Shop on eBay` (фейковый длинный
`data-listingid`, title = `Shop on eBay`). Пропускаем.
Sponsored-карточки берём как обычные.

### Item id

```
li.s-card                       — атрибут data-listingid
```

Реальные id — 12 цифр. Если `data-listingid` явно длиннее (16+) — это placeholder.

### Название

```
li.s-card  .s-card__title
```

После `.get_text(strip=True)` убрать суффикс `Opens in a new window or tab`.

### Состояние

```
li.s-card  .s-card__subtitle  .su-styled-text
```

Нормализация в `new` / `other`:
- `Brand New`, `New`, `New (Other)`, `Open Box`  →  `new`
- всё остальное (`Pre-owned`, `Used`, `For parts or not working`, …)  →  `other`

Сырое значение тоже сохраняем в `condition_raw`.

### Цена

```
li.s-card  .s-card__price
```

Парсим строку в `price` (float) и `currency`:
- `$` без префикса → USD
- `US $` → USD,  `CA $` → CAD,  `AU $` → AUD
- `£` → GBP,  `€` → EUR

Regex:

```
^(?P<cur>(?:US|CA|AU)?\s?[$£€]|EUR|GBP|CAD)\s?(?P<amount>[\d,]+(?:\.\d+)?)
```

Диапазон (`$10.00 to $15.00`) — два поля `price_min` / `price_max`.

### Стоимость доставки

⚠️ У строки доставки **нет отдельного стабильного класса**. Все «атрибутные» строки
карточки имеют одинаковый `.s-card__attribute-row`, вложенные span'ы —
`.su-styled-text.secondary.large`. Различить можно **только по тексту**.

Идём по всем `.s-card__attribute-row` карточки, матчим (первый матч побеждает):

```
^Free\b.*\b(delivery|shipping|postage|P&P)\b                               → 0.0
^\+?\s?(?P<cur>[A-Z]{0,3}\s?[$£€])\s?(?P<amount>[\d,]+(?:\.\d+)?)\s+(delivery|shipping|postage|P&P|shipping estimate)\b
^Shipping not specified$                                                    → None
```

⚠️ В `Free`-правиле ключевое слово доставки **обязательно**: в тех же
`.s-card__attribute-row` лежит `Free returns` — без требования keyword
наивный `^Free` ловит его как бесплатную доставку (баг).

Ключевые слова платной строки: `delivery` (основное на en-US, `+$X delivery`),
`shipping estimate` (`+$X shipping estimate`), реже `shipping`/`postage`/`P&P`.

`Shipping not specified` — продавец не указал доставку → `shipping_cost = None`
(легитимный кейс, не ошибка парсинга). `Local pickup` в карточках выдачи не
встречается (живёт в фильтрах сайдбара) — выбор «дешёвый без pickup» здесь
неприменим.

Поле:
- `shipping_cost` — float (Free = 0.0; платно = число; `Shipping not
  specified` / нет строки / неизвестный формат → None)

Проверено live (en-US, 300 карточек, 5 запросов): распределение —
`+$X delivery` 169, `+$X shipping estimate` 25, `Free International Shipping`
43, `Shipping not specified` 20, нет строки 3; ошибок классификации 0.

Подход хрупкий — при смене шаблона или другой локали промахнётся. Проверять
периодически на свежих SRP.

### Имя продавца

Два варианта вёрстки:

1. Top Rated Plus (со значком):

```
li.s-card  .s-card__program-badge-container--sellerOrStoreInfo  .su-styled-text
```

2. Без значка:

```
li.s-card  .su-card-container__attributes  .su-styled-text.primary.large
```

Берём **первый** из элементов.

Строка имеет вид: `<seller_name> NN.N% positive (NN.NK)`. Имя — первое «слово»:

```
^(?P<seller>\S+?)\s+\d+(?:\.\d+)?%\s+positiv
```

⚠️ Иногда на этом месте оказывается coupon-метка (текст промокода, не содержит
`% positive`). Фильтр по regex выше отсеивает её — обрабатываем только то, что
матчит.

### Картинка-превью

```
li.s-card  img.s-card__image
```

Атрибуты: `src` (превью ~140px), `srcset` (другие размеры).

---

## Заголовок результатов

```
h1.srp-controls__count-heading
```

Текст вида `46 results for 853762T01`. Достаём только число:

```
^([\d,]+)
```

---

## Сепаратор «Results matching fewer words»

В выдаче eBay сам разбивает карточки на две группы:

1. **Точные совпадения с поисковым запросом** — нужны нам.
2. **Похожие результаты** — eBay подсовывает их после баннера с текстом
   `Results matching fewer words`. Парсить их **не нужно**.

Баннер — это отдельный `<li>` (НЕ `s-card`) внутри того же `<ul>` с
выдачей. Селектор баннера:

```
li:has(section.su-notice span.BOLD)        ← где span.BOLD содержит "Results matching fewer words"
```

Можно надёжнее — текстовым фильтром:

```python
for li in soup.select(".srp-results > li"):
    sep = li.select_one("section.su-notice span.BOLD")
    if sep and "Results matching fewer words" in sep.get_text():
        break
    # обрабатываем только li.s-card до этого момента
```

**Правило:** парсим все `li.s-card`, идущие **до** этого `<li>` в DOM-порядке.
Всё после — игнорируем (это похожие, а не точные результаты).

---

## UI «Shipping to» — смена адреса доставки

Проверено live (декабрь 2025, US-прокси; дополнено июнь 2026). eBay
переименовал классы: старые `srp-shipping-location__flyout` /
`gh-ship-to__menu` больше не рендерятся, всё переехало на префикс
`s-zipcode-entry`.

⚠️ **Два варианта вёрстки, sticky на браузерный контекст** (внутри контекста
вариант не меняется между страницами/запросами; разные контексты случайно
попадают в разные группы; триггеры взаимоисключающие). Ниже — основной
(spotlight) вариант; A/B-вариант — в конце раздела. Зачем ZIP обязателен,
дефолт `00-001`, почему `_stpos` в URL не работает — [catalog_flow.md](catalog_flow.md).

### Триггер в левом сайдбаре

```
.x-refine-shipping-spotlight  button.s-zipcode-entry__btn
```

Полный HTML:

```html
<div class="x-refine-shipping-spotlight">
  <div class="s-zipcode-entry has-location-icon">
    <button class="s-zipcode-entry__btn s-zipcode-entry__btn--inline fake-link" type="button">
      <span class="clipped">Update your location</span>
      <svg>…location icon…</svg>
      Shipping to<span class="s-zipcode-entry__label">19701</span>
    </button>
  </div>
</div>
```

ZIP отдельно — внутри `.s-zipcode-entry__label`. На странице также есть
другие `button.s-zipcode-entry__btn` (для Local Pickup, для radio location в
секции LH_PrefLoc) — обязательно префиксуем `.x-refine-shipping-spotlight`,
иначе матчит лишнее.

### Модал «Update your location»

Появляется после клика по триггеру (рендерится лениво). Структура:

```
.s-zipcode-entry__modal--delivery
  .srp-shipping-location          ← форма с country/zip
  .s-zipcode-entry__apply
    button.btn.btn--secondary     ← Cancel
    button.btn.btn--primary       ← Apply
```

Из live-теста: оборачивающий контейнер — `div.lightbox-dialog__window`
(generic), внутри лежит `.s-zipcode-entry__modal--delivery`.

### Поле «Select country»

```
.s-zipcode-entry__modal--delivery select
```

⚠️ Значения опций — **числовые порядковые id** (`United States` = `value="1"`,
`Afghanistan` = `value="4"`, …), НЕ ISO-коды. `select_option(value="US")` не
работает. Выбираем **по тексту опции** (`United States - USA`):
`select_option(label="United States - USA")` или по подстроке `United States`.
Подтверждено live (en-US, июнь 2026): числовой id порядковый и может плыть —
завязываться на конкретное число нельзя.

### Поле «Zip code»

```
.s-zipcode-entry__modal--delivery input[type="text"]
```

### Кнопка Apply

```
.s-zipcode-entry__apply button.btn--primary
```

(Старого `input[type="submit"].btn--primary` больше нет — теперь обычный
`button`. Селектор `form.srp-shipping-location__form` тоже не работает —
форма как блок есть, но без класса `__form`. Cancel не парсим — он не нужен.)

### Нюансы флоу (подтверждено live)

- Модал «не visible» по меркам Playwright (нет offsetParent) — ждать
  `select` со `state="attached"`, не `visible`.
- Один клик по триггеру открывает модал за ~0.1–0.3с (8/8 live, ретрай не
  нужен; ранние «провалы клика» оказались A/B-контекстами, где этого
  триггера нет вовсе).
- После Apply eBay меняет URL и перерисовывает выдачу; подтверждение —
  по факту: ждать, пока текущий ZIP на странице станет равным заданному.

### A/B-вариант вёрстки

Когда контекст попал в A/B-группу, spotlight-блока нет вовсе. Селекторы:

| что | селектор |
|---|---|
| триггер | `button.shipping-entry` (текст «Update your location / Shipping to NNNNN») |
| страна | `div[role=dialog] select` — в диалоге несколько select; брать видимый со страновыми опциями (`"Country - ISO3"`) |
| zip | `input[name='_stpos']` |
| apply | `div[role=dialog] button.btn--primary` (видимый) |
| текущий ZIP | число из текста триггера («Shipping to 19701») — отдельного label нет |

Формат label страны общий для обоих вариантов: `United States - USA`.
Реализация обоих вариантов — `ebay_library/worker/zipcode.py` + `parsing/selectors.py`
(`ZipSpotlight` / `ZipAb`).

---

## Прочее

- URL объявления в выдаче — не парсим, собираем сами: `https://www.ebay.com/itm/{item_id}`.
- Пагинация, стоп-сигналы, дедуп между страницами — [catalog_flow.md](catalog_flow.md).
- «Located in …» парсим в `location` (опционален — eBay рендерит лениво/не
  всегда, см. catalog_flow). Зачёркнутая цена, возврат, рейтинг продавца,
  кол-во отзывов, категории сайдбара, сортировка, фильтры — не парсим.
- Антибот-страницы (`Pardon Our Interruption` / `Access Denied`) —
  [`antibot_localization.md`](antibot_localization.md).
