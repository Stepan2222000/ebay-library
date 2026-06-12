"""Запись результатов парсинга в БД ebay_data — тонкий клиент серверного API.

Вся логика хранения — в самой БД (проект ebay_data, db/schema.sql): серверные
функции ``apply_catalog_fetch`` / ``apply_item_snapshot`` делают апсерты,
диффы спецификаций/галереи, журнал изменений (триггеры) и death/resurrection
по misses. Здесь только сериализация наших dataclass'ов в jsonb и вызов —
никакой своей записи в таблицы (иначе задвоится журнал).

Контракты payload (зеркало моделей):
- каталог: ``Catalog`` одного артикула; каждой карточке добавляется
  ``"currency": "USD"`` (суммы уже в USD после fx — функция это проверяет);
- item: ``ItemPage`` как есть.

Ошибки (сеть/функция/валидация в БД) — критические, летят наружу без
ретраев: задача падает, переотдача — забота оркестратора. Reconnect'а нет.

Драйвер — asyncpg. pgbouncer-ready: вне явных транзакций asyncpg работает в
autocommit (один вызов функции = одна транзакция), а prepared statements
отключены (``statement_cache_size=0``) — при появлении pgbouncer меняется
только DSN.
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


class Store:
    """Клиент записи в ebay_data. Одно соединение, открывается лениво при
    первой записи; не потокобезопасен (один Store на воркера, как и сессия).
    Закрытие — ``close()`` (``run_worker`` зовёт сам)."""

    def __init__(self, dsn: str = EBAY_DATA_DSN):
        self._dsn = dsn
        self._conn: asyncpg.Connection | None = None

    async def _connection(self) -> asyncpg.Connection:
        if self._conn is None or self._conn.is_closed():
            self._conn = await asyncpg.connect(self._dsn, statement_cache_size=0)
            # jsonb прозрачно: dict → jsonb на входе, dict ← jsonb на выходе
            await self._conn.set_type_codec(
                "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
            )
        return self._conn

    async def apply_catalog(
        self,
        article: str,
        catalog: Catalog,
        *,
        zip: str,
        condition: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> dict:
        """Фетч каталога ОДНОГО артикула → ``apply_catalog_fetch``.

        Фильтры — те же, что были у ``fetch_catalog`` (по ним БД резолвит
        search-профиль; ``condition`` None → 'all'). Артикул обязан
        существовать в smart-каталоге — иначе функция бросает (критично).
        Возвращает статистику применения: ``{fetch_id, items_total,
        items_new, appeared, missed, deactivated, died}``."""
        payload = {
            "results_count": catalog.results_count,
            "items": [
                {"item_id": it.item_id, "title": it.title, "condition": it.condition,
                 "price": it.price, "currency": "USD", "shipping_cost": it.shipping_cost,
                 "seller": it.seller, "location": it.location, "image_url": it.image_url}
                for it in catalog.items
            ],
        }
        conn = await self._connection()
        return await conn.fetchval(
            "SELECT apply_catalog_fetch($1, $2, $3, $4::numeric, $5::numeric, $6::jsonb)",
            article, zip, condition or "all", min_price, max_price, payload,
        )

    async def apply_item(self, item: ItemPage, *, zip: str) -> dict:
        """PDP-снапшот → ``apply_item_snapshot``. Возвращает статистику:
        ``{item_id, is_new, specifics: {...}, images: {...}}``."""
        payload = {
            "item_number": item.item_number, "title": item.title,
            "condition": item.condition, "price_usd": item.price_usd,
            "shipping_cost": item.shipping_cost, "seller": item.seller,
            "location": item.location, "description": item.description,
            "last_updated": item.last_updated, "specifics": item.specifics,
            "image_urls": item.image_urls,
        }
        conn = await self._connection()
        return await conn.fetchval(
            "SELECT apply_item_snapshot($1, $2::jsonb)", zip, payload,
        )

    async def apply_item_ended(self, item_id: str) -> dict:
        """Листинг завершён (ENDED) → ``apply_item_ended`` — железная смерть
        (``dead_reason='ended'``, мгновенно, минуя misses-порог). Товара не
        было в БД → создаётся «родившимся мёртвым» (только id). Возвращает
        ``{item_id, was_new, dead_reason}``."""
        conn = await self._connection()
        return await conn.fetchval(
            "SELECT apply_item_ended($1::bigint)", int(item_id),
        )

    async def close(self) -> None:
        if self._conn is not None and not self._conn.is_closed():
            await self._conn.close()
