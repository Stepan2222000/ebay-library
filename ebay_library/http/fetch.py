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
§7.2); прочий статус (включая ``404`` — ended-логику добавим позже) → ``TransportError``
(громко падаем, чтобы поймать живьём).
"""

from __future__ import annotations

import asyncio

from curl_cffi import AsyncSession
from curl_cffi.requests import exceptions as _cex

from ..errors import TransportError

_ITM_URL = "https://itm.ebaydesc.com/itm/{item_id}"
_DESC_URL = "https://itm.ebaydesc.com/itmdesc/{item_id}"
_HEADERS = {"Accept-Language": "en-US"}      # обязателен — иначе локаль гео (см. шапку)
_RETRIES = 3                                  # ретраи транзиента (SPEC.md §7.1)
_BACKOFF_S = 0.3                              # пауза = _BACKOFF_S * номер попытки
_TIMEOUT_S = 20.0


def make_item_session(*, max_clients: int, timeout: float = _TIMEOUT_S) -> AsyncSession:
    """Долгоживущая сессия item-транспорта (одна на воркер). Без impersonate, en-US.

    ``max_clients`` — потолок одновременных curl-хендлов (= конкуренция ``C``);
    держать его на уровне адаптивной конкуренции (SPEC.md §8)."""
    return AsyncSession(max_clients=max_clients, timeout=timeout, headers=_HEADERS)


async def _get_text(session: AsyncSession, url: str) -> str:
    last: object = None
    for attempt in range(_RETRIES):
        try:
            r = await session.get(url)
        except _cex.RequestException as e:        # сеть/таймаут/DNS/прокси → транзиент
            last = e
            await asyncio.sleep(_BACKOFF_S * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.text
        if r.status_code == 503:                  # транзиент (у ebaydesc мигает)
            last = f"HTTP 503 {url}"
            await asyncio.sleep(_BACKOFF_S * (attempt + 1))
            continue
        raise TransportError(f"unexpected status {r.status_code} for {url}")
    raise TransportError(f"retries exhausted ({_RETRIES}) for {url}: {last!r}")


async def fetch_item(session: AsyncSession, item_id: str) -> str:
    """Сырой HTML страницы товара (основной запрос, SPEC.md §4.2)."""
    return await _get_text(session, _ITM_URL.format(item_id=item_id))


async def fetch_description(session: AsyncSession, item_id: str) -> str:
    """Сырой HTML описания (itmdesc, SPEC.md §4.5). Зовётся ТОЛЬКО при успехе
    ``fetch_item`` — порядок оркеструет лейн (этап 3)."""
    return await _get_text(session, _DESC_URL.format(item_id=item_id))
