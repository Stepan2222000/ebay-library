"""Запись результатов парсинга в БД ebay_data — тонкий клиент серверного API.

Вся логика хранения — в самой БД (проект ebay_data): серверные функции
``apply_catalog_fetch`` / ``apply_item_snapshot`` делают апсерты, диффы спецификаций/
галереи, журнал изменений (триггеры) и death/resurrection. Здесь только сериализация
наших dataclass'ов в jsonb и вызов — никакой своей записи в таблицы.

Владение полями (SPEC.md §2.3): **каталог** пишет цену (USD) и доставку
(``item_shipping``); **item-снапшот в Mode 1 их НЕ пишет** — ``apply_item`` шлёт
payload без ``price_usd``/``shipping_cost`` (серверная ``apply_item_snapshot`` их
тоже не трогает — обновлено на этапе 5; см. SPEC.md §4.4). Mode 2 вернёт их.

Пул: новые item/каталог-воркеры пишут **N потребителями параллельно**, поэтому держим
**asyncpg-пул** (одно соединение не потокобезопасно — ``InterfaceError`` при
конкуренции). Каждый ``apply_*`` берёт соединение из пула на вызов. jsonb-codec и
``statement_cache_size=0`` (pgbouncer-ready, конвенция проекта) — в ``init`` пула.
Item-воркер ставится **рядом с БД** (низкий RTT → запись дешёвая, батч не нужен;
этап 5, решение A). ``apply_item_ended`` добавим на ended-этапе.
"""

from __future__ import annotations

import json
import os

import asyncpg

from .models import Catalog, ItemPage

EBAY_DATA_DSN = os.environ.get(
    "EBAY_DATA_DSN",
    "postgresql://admin:Password123@194.164.245.107:5415/ebay_data",
)


async def _init_conn(conn: asyncpg.Connection) -> None:
    """Инициализация соединения пула: jsonb прозрачно (dict ↔ jsonb)."""
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


def _catalog_payload(catalog: Catalog) -> dict:
    """``Catalog`` → payload ``apply_catalog_fetch``. Каждой карточке — ``currency:USD``
    (суммы уже в USD после fx, SPEC.md §5.4). Каталог владеет ценой и доставкой."""
    return {
        "results_count": catalog.results_count,
        "items": [
            {"item_id": it.item_id, "title": it.title, "condition": it.condition,
             "price": it.price, "currency": "USD", "shipping_cost": it.shipping_cost,
             "seller": it.seller, "location": it.location, "image_url": it.image_url}
            for it in catalog.items
        ],
    }


def _item_payload(item: ItemPage) -> dict:
    """``ItemPage`` → payload ``apply_item_snapshot``. Mode 1: ``price_usd``/
    ``shipping_cost`` НЕ шлём — каталог ими владеет (SPEC.md §2.3/§4.4)."""
    return {
        "item_number": item.item_number, "title": item.title, "condition": item.condition,
        "seller": item.seller, "location": item.location, "description": item.description,
        "last_updated": item.last_updated, "specifics": item.specifics,
        "image_urls": item.image_urls,
    }


class Store:
    """Клиент записи в ebay_data на пуле соединений. Один ``Store`` на воркер; методы
    ``apply_*`` безопасны при параллельных вызовах (берут соединение из пула на вызов).
    Пул создаётся лениво при первой записи; закрытие — ``close()``."""

    def __init__(self, dsn: str = EBAY_DATA_DSN, *, min_size: int = 2, max_size: int = 16):
        self._dsn = dsn
        self._min = min_size
        self._max = max_size
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=self._min, max_size=self._max,
                statement_cache_size=0, init=_init_conn,
            )
        return self._pool

    async def apply_catalog(
        self, article: str, catalog: Catalog, *, zip: str,
        condition: str | None = None, min_price: float | None = None,
        max_price: float | None = None,
    ) -> dict:
        """Фетч каталога ОДНОГО артикула → ``apply_catalog_fetch`` (каталог владеет
        ценой/доставкой). Артикул обязан существовать в smart-каталоге, иначе функция
        бросает (критично). Возвращает статистику применения."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT apply_catalog_fetch($1, $2, $3, $4::numeric, $5::numeric, $6::jsonb)",
                article, zip, condition or "all", min_price, max_price,
                _catalog_payload(catalog),
            )

    async def apply_item(self, item: ItemPage, *, zip: str) -> dict:
        """PDP-снапшот → ``apply_item_snapshot`` (без цены/доставки — Mode 1).
        ``zip`` принимается для совместимости сигнатуры (в Mode 1 не используется
        серверной функцией). Возвращает статистику применения."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT apply_item_snapshot($1, $2::jsonb)", zip, _item_payload(item),
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
