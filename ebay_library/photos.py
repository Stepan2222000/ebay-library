"""Фото товара: скачивание из БД-галереи + опц. заливка в S3 и запись ``s3_key``.

Источник URL — БД (``item_images.ebay_url``, заполняет ``apply_item`` на этапе парсинга);
этот метод лишь скачивает байты и (опц.) заливает их в S3, проставляя ``s3_key``
существующим строкам. Это «сторонняя программа» из контура фото (db/README ebay_data):
сам воркер парсинга ``s3_key`` не пишет.

Контракт ``fetch_photos`` (решения — согласованы):
- ``count`` — первые N фото по галерее (``idx`` 0..N-1); ``None`` — все.
- ``upload=False`` → БД/S3 не трогаем: ``Photo`` с байтами (``content``), ``s3_url`` =
  текущий ``s3_key`` из БД (или None), ``uploaded=False``.
- ``upload=True`` → среди первых N строки с НЕпустым ``s3_key`` пропускаем (идемпотентно),
  остальные качаем → заливаем (оригинал JPEG, ключ ``{item_id}/{url_hash}.jpg``) →
  ``UPDATE s3_key``. В ответе байты НЕ держим (``content=None``) — только метаданные.
- Fail-fast: любой не-2xx/сбой скачивания или заливки летит наружу (как у парсинга).
- Мёртвый листинг (``is_dead``) при ``upload=True`` → ошибка ДО скачивания (не плодим
  осиротевшие объекты в S3); guard в БД — второй рубеж. Товара нет в ``items`` → ошибка.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .http.images import fetch_images
from .s3 import S3Photos
from .store import Store

_log = logging.getLogger("ebay_library")


@dataclass(frozen=True, slots=True)
class Photo:
    """Одно фото галереи. ``content`` — байты JPEG, заполнены ТОЛЬКО при ``upload=False``
    (в режиме заливки не держим в памяти). ``s3_url`` — публичный URL в S3 (существующий
    или только что залитый), иначе None. ``uploaded`` — залит ли ИМЕННО этим вызовом."""

    idx: int
    ebay_url: str
    url_hash: str            # hex md5(ebay_url)
    content: bytes | None
    s3_url: str | None
    uploaded: bool


async def fetch_photos(
    item_id: str | int,
    count: int | None = None,
    *,
    store: Store,
    upload: bool = False,
    s3: S3Photos | None = None,
) -> list[Photo]:
    """Скачивает первые ``count`` фото товара ``item_id`` (URL — из БД-галереи).

    ``upload=False`` (по умолчанию) — вернуть байты, БД/S3 не трогать. ``upload=True`` —
    залить недостающие (без ``s3_key``) в S3 и проставить ``s3_key``; вернуть метаданные.
    ``s3`` (переиспользуемый ``S3Photos``) нужен только при ``upload=True``; ``None`` →
    дефолтный (боевой MinIO). Полный контракт — в шапке модуля."""
    is_dead = await store.item_is_dead(item_id)
    if is_dead is None:
        raise ValueError(f"item {item_id} not found in ebay_data (never parsed)")
    if upload and is_dead:
        raise ValueError(f"cannot upload photos for dead item {item_id} (is_dead)")

    rows = await store.image_rows(item_id, limit=count)
    if not rows:
        return []

    if not upload:
        contents = await fetch_images([r["ebay_url"] for r in rows])  # fail-fast
        return [
            Photo(idx=r["idx"], ebay_url=r["ebay_url"], url_hash=r["url_hash"],
                  content=c, s3_url=r["s3_key"], uploaded=False)
            for r, c in zip(rows, contents)
        ]

    # upload=True: уже залитые (непустой s3_key) пропускаем — идемпотентно
    s3 = s3 or S3Photos()
    targets = [r for r in rows if r["s3_key"] is None]
    contents = await fetch_images([r["ebay_url"] for r in targets])  # fail-fast

    async def _upload_one(row: dict, content: bytes) -> str:
        key = f"{item_id}/{row['url_hash']}.jpg"
        url = await s3.upload_jpeg(key, content)                      # fail-fast
        if not await store.set_image_s3_key(item_id, row["url_hash"], url):
            _log.warning("s3_key not recorded: row vanished item=%s url_hash=%s",
                         item_id, row["url_hash"])
        return url

    new_urls = await asyncio.gather(*(_upload_one(r, c) for r, c in zip(targets, contents)))
    fresh = {r["url_hash"]: u for r, u in zip(targets, new_urls)}

    return [
        Photo(idx=r["idx"], ebay_url=r["ebay_url"], url_hash=r["url_hash"], content=None,
              s3_url=fresh.get(r["url_hash"], r["s3_key"]),
              uploaded=r["url_hash"] in fresh)
        for r in rows
    ]
