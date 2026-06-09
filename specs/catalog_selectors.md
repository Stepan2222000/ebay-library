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

## ZIP доставки — параметр URL (не UI)

Смена ZIP через UI **удалена**. ZIP задаётся параметром `_stpos=<zip>` в URL
выдачи (`config.build_search_url`, см. [catalog_flow.md](catalog_flow.md)) — это
**реальная локация**, не косметика: доставка пересчитывается под ZIP
(подтверждено live на US-прокси, июнь 2026 — 19701 vs Аляска 99950 дают разные
суммы доставки/число результатов). Состояние — `LH_ItemCondition` (`3`=new,
`3000`=used), цена — `_udlo`/`_udhi`. Селекторов ZIP-флоу в коде больше нет.

⚠️ Вёрстка SRP зависит от **страны IP** (не от браузера — проверено Chrome и
CloakBrowser, локально и на сервере под Xvfb). US-IP → каноническая вёрстка
(`srp-mag-ui-variant2`), цены USD. Не-US IP (напр. CZ) → локализованная вёрстка
с топ-бар flyout `.srp-shipping-location__flyout` и местной валютой;
URL-параметром на US-вёрстку не переключить (только US-IP). Воркер ходит через
US-прокси, поэтому flyout-вариант в проде не задействуется.

---

## Прочее

- URL объявления в выдаче — не парсим, собираем сами: `https://www.ebay.com/itm/{item_id}`.
- Пагинация, стоп-сигналы, дедуп между страницами — [catalog_flow.md](catalog_flow.md).
- «Located in …» парсим в `location` (опционален — eBay рендерит лениво/не
  всегда, см. catalog_flow). Зачёркнутая цена, возврат, рейтинг продавца,
  кол-во отзывов, категории сайдбара, сортировка, фильтры — не парсим.
- Антибот-страницы (`Pardon Our Interruption` / `Access Denied`) —
  [`antibot_localization.md`](antibot_localization.md).
