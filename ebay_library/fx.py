"""Конвертация валют каталога в USD через fx-микросервис (HTTP).

Чистый парсер (Слой 1) валюту НЕ переводит — отдаёт SrpCard с native-суммой и
сырым токеном ('$','C $','EUR'…). Перевод — здесь: дёргаем публичный эндпоинт
``GET /convert?amount=&from=<токен>&to=USD``. Эндпоинт сам резолвит написание
(см. fx.currency_aliases) и держит курсы в своём кэше (TTL) — кэшировать на
нашей стороне не нужно.

Без фолбеков: неизвестная валюта (404) или недоступность сервиса
(сеть/таймаут/5xx) → исключение httpx наружу (в fetch_catalogs изолируется
per-подзадача).

URL эндпоинта: ``FX_API_URL`` (env), дефолт — публичный адрес сервиса.
Параллелизм без лимита — ограничен только пулом httpx (воркеры ходят с
серверов рядом с сервисом, латентность низкая).
"""

from __future__ import annotations

import asyncio
import os

import httpx

from .parsing.models import CatalogItem, SrpCard

FX_API_URL = os.environ.get("FX_API_URL", "http://194.164.245.107:8092")
_TIMEOUT = httpx.Timeout(10.0)
# без лимита одновременных соединений (только пул httpx)
_LIMITS = httpx.Limits(max_connections=None, max_keepalive_connections=None)


async def _convert(client: httpx.AsyncClient, amount: float, currency_raw: str) -> float:
    r = await client.get(
        "/convert", params={"amount": amount, "from": currency_raw, "to": "USD"}
    )
    r.raise_for_status()  # 404 (нет валюты) / 5xx → наружу
    return float(r.json()["result"])


async def convert_cards(cards: list[SrpCard], *, base_url: str = FX_API_URL) -> list[CatalogItem]:
    """SrpCard (native price + currency_raw) → CatalogItem (price/shipping в USD).

    Собирает уникальные (сумма, валюта) пары (цены + платные доставки), дедуп
    одинаковых, шлёт ``/convert`` параллельно (без лимита), затем разносит USD
    обратно по карточкам. Free (0.0) и «не указана» (None) доставка не
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
