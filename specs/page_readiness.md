# Детект страницы и готовность

Единственный владелец понятия «что за страница» — `html/page_state.py`
(Слой 1). Antibot-страницы и их `<title>` —
[antibot_localization.md](antibot_localization.md).

---

## Принцип: тип — по URL и title, НЕ по DOM

- `classify(url)` → `home | srp | item | pardon | unknown`. Pardon виден по
  пути `/splashui/challenge` (eBay редиректит туда исходный запрос).
- `detect_antibot(title)` → `pardon | access_denied | error_page | None`.
  Access Denied и Error Page приходят на обычном `/sch/`-`/itm/` URL —
  по URL их не отличить, только по `<title>`.
- `detect_state(url, title)` объединяет оба сигнала в `PageState`.
- `is_ready(state, expect)` — не antibot И тип совпал с ожидаемым.

`page_state` остаётся чистым (никаких DOM-селекторов) — это позволяет
тестировать его без браузера и не дублировать детект по коду.

## Политики блоков (browser-слой)

Классификацию «что лечится заменой страницы» делает `browser/session.py`
(`_retryable`): только блокировка eBay и транспортная смерть page.

| блок | политика |
|---|---|
| PARDON | JS-челлендж проходит сам — ждём в цикле до 180с (`PARDON_TIMEOUT_S`); не прошёл → TimeoutError, КРИТИЧНО |
| ERROR_PAGE | `ErrorPageError` — КРИТИЧНО (наружу, задача и воркер падают) |
| ACCESS_DENIED | `AccessDeniedError` — сессия меняет страницу (пауза `PAGE_DELAY_S` → `get_page` → прогрев → продолжение с того же места, без лимита) |
| транспорт (`net::ERR_*`, `Target crashed`, `TargetClosedError`) | как ACCESS_DENIED — замена страницы |
| таймауты (Playwright TimeoutError, якоря, iframe) | КРИТИЧНО — транспортом не считаются |
| UNKNOWN url | сохраняем HTML в `ebay_data/unknown/` для разбора, ждём дальше (до общего таймаута → критично) |

Таксономия транспортных ошибок снята живьём (Playwright 1.60): `net::ERR_*`
→ `Error` с `net::ERR` в сообщении; закрытые page/context/browser →
`TargetClosedError` (из `playwright.async_api` не экспортируется — матчим по
имени типа); операции на крашнутой странице → `Error` 'Target crashed'.

## Готовность = тип страницы + DOM-якоря

`wait_until_ready(page, expect)` (`browser/readiness.py`):
1. цикл по url+title до «не-блок и целевой тип» (политики выше);
2. затем ожидание ВСЕХ DOM-якорей типа (`READY_ANCHORS`, до 30с
   `ANCHOR_TIMEOUT_S`) — всё, что планируем парсить, должно быть в DOM.

Якоря ссылаются на боевые селекторы из `selectors.py` (те же, что использует
парсер — без отдельных «anchor»-дублей):

- **HOME**: строка поиска.
- **SRP**: счётчик результатов + карточка `li.s-card[data-listingid]`.
- **ITEM**: title, item number, цена, condition, shipping-модуль,
  seller-карточка, specifics, картинки карусели, `iframe#desc_ifr`.

Сигнал «есть ли данные/конец пагинации» — НЕ здесь: это отвечает парсер
каталога (счётчик/карточки/сепаратор), см. [catalog_flow.md](catalog_flow.md).
