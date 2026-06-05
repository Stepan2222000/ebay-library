# Поток товара (item / PDP)

Селекторы полей — [item_selectors.md](item_selectors.md). Готовность/блоки —
[page_readiness.md](page_readiness.md). Здесь — контракт потока: два HTML,
правила полей, добыча.

---

## Два HTML на один товар

Описание товара живёт в **отдельном iframe** (`iframe#desc_ifr` →
`itm.ebaydesc.com`) — в HTML основной страницы его текста НЕТ (видимый на
экране текст — отрендеренный iframe). Поэтому парсер принимает два документа:

```
parse_item_page(main_html, description_html=None) -> ItemPage
```

- `main_html` — все поля;
- `description_html` — `frame.content()` iframe-фрейма: передан → извлекаем
  текст (bs4, без script/style, `\n`-разделитель), не передан → `description=""`.

Браузер грузит iframe сам при открытии страницы (доп. запросов не нужно), но
документ фрейма появляется не сразу: сначала `about:blank`, затем реальный
`ebaydesc.com`. Добыча (`worker/fetch.py::_description_html`): ждать фрейм с этим
host + `domcontentloaded` (до 15с), нет → TimeoutError (фатально).

## Правила полей

- **seller — username**, не display-name. Видимый текст карточки продавца —
  витрина («Florida Tool Shed»), username (`fltoolbox`) — в ссылках продавца:
  `_ssn=<username>` либо `/str/<username>` (магазины). Формат совпадает с
  каталогом — общий ключ для сопоставления.
- **Цена — всегда USD**: есть `.x-price-approx` («Approximately US $X», intl)
  → берём его; нет (US-листинг) → primary. Суффикс `/ea`,`/lot` отбрасываем.
  Native-валюту не храним.
- **Доставка — итог в USD**: `Free…` → 0.0; intl → число из `(approx US $X)`;
  US → `US $X` в начале строки. Не выбили → ParseError (None не ставим).
  Method/carrier не выделяем (хрупко из-за маркетингового хвоста
  «Shop with confidence…», и не нужно).
- **condition** — нормализация через общий `normalize.py` (тот же, что
  каталог): new/other.
- **specifics** — вся таблица key→value, как есть.
- **image_urls** — full-size из `data-zoom-src`; eBay дублирует каждое фото в
  карусели — дедуп с сохранением порядка. Скачивание фото — отдельная тема.
- **last_updated** — единственное опциональное: блок «Last updated on» есть
  только у редактированных листингов. Срезаем префикс и хвост
  «View all revisions». Строка-дата как есть (без datetime-парсинга).
  ⚠️ текст «Last updated on» встречается и в `<script>` (JS i18n) — узлы
  внутри script пропускать.
- location обязателен (на PDP стабилен; универсальный поиск
  «Located in:» по SECONDARY-span — контейнер бывает разный:
  `--shipping` / `--legalShipping`).

## Добыча (`fetch_item`)

```
goto /itm/{id} → wait_until_ready(ITEM) → main_html → description_html → parse
```

Готовность ITEM включает `iframe#desc_ifr` как якорь (тег в DOM), но загрузку
документа фрейма ждёт отдельно `_description_html` — готовность универсальна,
специфика iframe в item-методе.

После каталога items автоматически НЕ парсим — только по отдельной задаче.
