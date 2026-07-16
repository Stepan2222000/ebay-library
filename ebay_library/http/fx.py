"""Конвертация валют каталога в USD через fx-микросервис (HTTP).

Скопировано из старого ``ebaylib`` (SPEC.md §12, §5.4). Чистый парсер (Слой 1)
валюту НЕ переводит — отдаёт SrpCard с native-суммой и сырым токеном ('$','C $',
'EUR'…). Перевод — здесь: дёргаем публичный эндпоинт
``GET /convert?amount=&from=<токен>&to=USD``. Эндпоинт сам резолвит написание и
держит курсы в своём кэше (TTL).

Сетевые обрывы (сервис под параллельным залпом сбрасывает часть соединений —
83 прод-падения: RemoteProtocol/Connect/ReadError, все из ``_convert``) —
транзиент: до ``_RETRIES`` попыток с короткой паузой. Ответ сервиса остаётся
честным фаталом без повторов: неизвестная валюта (404) или 5xx → исключение
наружу, валит задачу целиком.

URL эндпоинта: ``FX_API_URL`` (env), дефолт — публичный адрес сервиса.
"""

from __future__ import annotations

import asyncio
import os

import httpx

from ..models import CatalogItem, SrpCard

FX_API_URL = os.environ.get("FX_API_URL", "http://2.27.20.221:8092")
_TIMEOUT = httpx.Timeout(10.0)
# без лимита одновременных соединений (только пул httpx)
_LIMITS = httpx.Limits(max_connections=None, max_keepalive_connections=None)
_RETRIES = 3          # попыток на сетевой обрыв (transport-уровень)
_RETRY_PAUSE_S = 0.5  # пауза между попытками (растёт линейно)


async def _convert(client: httpx.AsyncClient, amount: float, currency_raw: str) -> float:
    for attempt in range(1, _RETRIES + 1):
        try:
            r = await client.get(
                "/convert", params={"amount": amount, "from": currency_raw, "to": "USD"}
            )
        except httpx.TransportError:  # обрыв/таймаут соединения — транзиент
            if attempt == _RETRIES:
                raise
            await asyncio.sleep(_RETRY_PAUSE_S * attempt)
            continue
        r.raise_for_status()  # 404 (нет валюты) / 5xx → наружу, без повторов
        return float(r.json()["result"])


async def convert_cards(cards: list[SrpCard], *, base_url: str = FX_API_URL) -> list[CatalogItem]:
    """SrpCard (native price + currency_raw) → CatalogItem (price/shipping в USD).

    Собирает уникальные (сумма, валюта) пары (цены + платные доставки), дедуп
    одинаковых, шлёт ``/convert`` параллельно (без лимита), затем разносит USD
    обратно по карточкам. Free (0.0) и отсутствующая (None) доставка не
    конвертируются — остаются как есть. Любой сбой fx пробрасывается наружу."""
    if not cards:
        return []

    pairs: set[tuple[float, str]] = set()
    for c in cards:
        pairs.add((c.price, c.currency_raw))
        if c.shipping_cost:  # 0.0 (Free) и None пропускаем — переводить нечего
            pairs.add((c.shipping_cost, c.currency_raw))
    pairs_list = list(pairs)

    async with httpx.AsyncClient(base_url=base_url, timeout=_TIMEOUT, limits=_LIMITS) as client:
        results = await asyncio.gather(
            *(_convert(client, amount, cur) for amount, cur in pairs_list)
        )
    usd = dict(zip(pairs_list, results))

    out: list[CatalogItem] = []
    for c in cards:
        ship_usd = usd[(c.shipping_cost, c.currency_raw)] if c.shipping_cost else c.shipping_cost
        out.append(
            CatalogItem(
                item_id=c.item_id,
                title=c.title,
                condition=c.condition,
                price=usd[(c.price, c.currency_raw)],
                shipping_cost=ship_usd,
                seller=c.seller,
                location=c.location,
                image_url=c.image_url,
            )
        )
    return out
