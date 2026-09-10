"""HTTP-транспорт товара (этап 2) — itm.ebaydesc.com, без браузера (SPEC.md §3.1, §4.1).

Тупой IO: GET страницы товара и её описания с ретраями транзиента. Парсинг — Слой 1
(html/item), порядок запросов — лейн (этап 3). Здесь только сеть.

Решения, проверенные живьём (этап 2):
- **curl_cffi.AsyncSession БЕЗ ``impersonate``.** Для un-walled ebaydesc имперсонация
  не нужна и **вредна**: с ``impersonate="chrome"`` eBay отдаёт другую/бо́льшую
  страницу, на которой ломается извлечение встроенной JSON-модели (балансировщик
  берёт не тот ``modules``) → ParseError на части товаров. Без неё — чистый разбор.
- **Обязателен ``Accept-Language: en-US``** — иначе eBay отдаёт страницу в локали гео
  запроса, и title/specifics приходят не на английском.
- **Сессия — долгоживущая, ОДНА на воркер** (``max_clients`` = конкуренция ``C``),
  функции её принимают; per-call сессии не плодим (session-per-request у curl_cffi →
  исчерпание коннектов/портов, особенно через прокси). Создаётся ``make_item_session``.

Исходы (только пойманные на практике, SPEC.md §11.1): ``200`` → текст; ``503`` /
сетевой сбой / таймаут → ретрай (§7.1), исчерпание → ``TransportError`` (критично,
§7.2); ``404`` основного запроса (``fetch_item``) → ``ListingNotFoundError`` (страницы
нет; ended-решение — у оркестратора по повтору); прочий статус → ``TransportError``.

``418`` (с 2026-08-06, проверено живьём 2026-09-08..10): пустой ответ (content-length 0)
от двух узлов Akamai (``x-ebay-pop-id`` SLBLVSAZ01 / SLBRNOAZ05), которые режут путь
``/itm/`` для наших адресов — не зависит от товара, заголовков, TLS-отпечатка, версии
HTTP, объёма трафика (не спадает ни за час, ни за сутки простоя); ``/itmdesc/`` те же
узлы пропускают. Узел закрепляется за TCP-соединением (keep-alive), поэтому повтор
на той же сессии почти всегда даёт 418 снова → повторяем через **новое соединение**
с бэкоффом ``_BACKOFF_418_S``. На 100 товарах approved-очереди: без ретрая 62–70
записанных, с ретраем 90–95 (остаток — следующий круг, pdp_seen_at остаётся NULL).
"""

from __future__ import annotations

import asyncio

from curl_cffi import AsyncSession
from curl_cffi.requests import exceptions as _cex

from ..errors import ListingNotFoundError, TransportError

_ITM_URL = "https://itm.ebaydesc.com/itm/{item_id}"
_DESC_URL = "https://itm.ebaydesc.com/itmdesc/{item_id}"
_HEADERS = {"Accept-Language": "en-US"}      # обязателен — иначе локаль гео (см. шапку)
_RETRIES = 3                                  # ретраи транзиента (SPEC.md §7.1)
_BACKOFF_S = 0.3                              # пауза = _BACKOFF_S * номер попытки
_BACKOFF_418_S = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 48.0)  # 418 → повтор через НОВОЕ соединение
# (см. шапку). 7 шагов, а не 5: плохой узел залипает и на новых соединениях; на одной
# сотне approved подряд: 5 шагов → 2 и 7 исчерпаний, 7 шагов → 0 (2026-09-10).
_TIMEOUT_S = 20.0


def make_item_session(*, max_clients: int, timeout: float = _TIMEOUT_S) -> AsyncSession:
    """Долгоживущая сессия item-транспорта (одна на воркер). Без impersonate, en-US.

    ``max_clients`` — потолок одновременных curl-хендлов (= конкуренция ``C``);
    держать его на уровне адаптивной конкуренции (SPEC.md §8)."""
    return AsyncSession(max_clients=max_clients, timeout=timeout, headers=_HEADERS)


def _fresh_connection(session: AsyncSession) -> AsyncSession:
    """Одноразовая сессия = гарантированно НОВОЕ TCP-соединение (другой узел Akamai),
    с теми же прокси/заголовками, что у долгоживущей. Закрывает вызывающий."""
    return AsyncSession(max_clients=1, timeout=_TIMEOUT_S,
                        headers=dict(session.headers), proxies=dict(session.proxies or {}))


async def _get_text(session: AsyncSession, url: str, *, ended_id: str | None = None) -> str:
    last: object = None
    transient = 0                 # сеть/таймаут/503 — счётчик _RETRIES
    teapot = 0                    # 418 — отдельный счётчик, повтор через новое соединение
    fresh: AsyncSession | None = None
    try:
        while True:
            try:
                r = await (fresh or session).get(url)
            except _cex.RequestException as e:    # сеть/таймаут/DNS/прокси → транзиент
                last = e
                transient += 1
                if transient >= _RETRIES:
                    break
                await asyncio.sleep(_BACKOFF_S * transient)
                continue
            if r.status_code == 200:
                return r.text
            if r.status_code == 503:              # транзиент (у ebaydesc мигает)
                last = f"HTTP 503 {url}"
                transient += 1
                if transient >= _RETRIES:
                    break
                await asyncio.sleep(_BACKOFF_S * transient)
                continue
            if r.status_code == 404 and ended_id is not None:
                # страницы товара нет — не парсим, не ретраим (ebaydesc в пределах минут
                # стабилен). Ended-решение — у оркестратора по повтору (см. ListingNotFoundError).
                raise ListingNotFoundError(ended_id)
            if r.status_code == 418:
                # плохой узел Akamai, закреплён за соединением (см. шапку) → бэкофф и
                # повтор через НОВОЕ соединение; исчерпание → как прочий статус
                # (текст «unexpected status 418» — лейн классифицирует его как status).
                if teapot >= len(_BACKOFF_418_S):
                    raise TransportError(
                        f"unexpected status 418 after {teapot} fresh-connection retries for {url}")
                if fresh is not None:
                    await fresh.close()
                fresh = _fresh_connection(session)
                await asyncio.sleep(_BACKOFF_418_S[teapot])
                teapot += 1
                continue
            raise TransportError(f"unexpected status {r.status_code} for {url}")
        raise TransportError(f"retries exhausted ({_RETRIES}) for {url}: {last!r}")
    finally:
        if fresh is not None:
            await fresh.close()


async def fetch_item(session: AsyncSession, item_id: str) -> str:
    """Сырой HTML страницы товара (основной запрос, SPEC.md §4.2). 404 →
    ``ListingNotFoundError`` (листинга нет — оркестратор считает промахи)."""
    return await _get_text(session, _ITM_URL.format(item_id=item_id), ended_id=str(item_id))


async def fetch_description(session: AsyncSession, item_id: str) -> str:
    """Сырой HTML описания (itmdesc, SPEC.md §4.5). Зовётся ТОЛЬКО при успехе
    ``fetch_item`` — порядок оркеструет лейн (этап 3)."""
    return await _get_text(session, _DESC_URL.format(item_id=item_id))
