"""Заливка фото товара в S3/MinIO (бакет ``ebay-data-photos``) — HTTP-IO без браузера.

Дополняет ``http/images`` (скачивание): сюда отдаём уже скачанные байты. Не Слой 1/2.
``boto3`` синхронный и thread-safe — клиент строим один раз и переиспользуем (один
``S3Photos`` на воркер, как ``Store``), ``put_object`` гоним в ``asyncio.to_thread``.

Конвенция (db/README проекта ebay_data): ключ объекта ``{item_id}/{hex(md5(ebay_url))}.jpg``
(грузим оригинал JPEG как есть), ``s3_key`` в БД = ПОЛНЫЙ публичный URL. ``endpoint``
(куда заливаем) и ``public_base_url`` (что пишем в ``s3_key``) — раздельны: если позже
перед MinIO встанет CDN/домен, меняется только ``public_base_url`` без перезаливки.
Бакет считаем существующим (public-read) — из библиотеки его не создаём.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import boto3
from botocore.client import Config as _BotoConfig

# Дефолты — боевой MinIO (как DSN в store.py); env-override EBAY_S3_*.
# Заливка (endpoint) и публичный URL для s3_key (public_base_url) РАЗДЕЛЕНЫ: внутри
# docker-сети заливать можно по http://minio:9000 (env EBAY_S3_ENDPOINT), но в s3_key
# пишем ВНЕШНИЙ http://2.27.20.221:9000 — иначе ссылка не резолвится снаружи docker.
_DEF_ENDPOINT = os.environ.get("EBAY_S3_ENDPOINT", "http://2.27.20.221:9000")
_DEF_BUCKET = os.environ.get("EBAY_S3_BUCKET", "ebay-data-photos")
_DEF_ACCESS_KEY = os.environ.get("EBAY_S3_ACCESS_KEY", "admin")
_DEF_SECRET_KEY = os.environ.get("EBAY_S3_SECRET_KEY", "Password123")
_DEF_PUBLIC_BASE = os.environ.get(  # внешний базовый URL для s3_key (читается снаружи)
    "EBAY_S3_PUBLIC_BASE_URL", "http://2.27.20.221:9000/ebay-data-photos"
)


@dataclass(frozen=True, slots=True)
class S3Config:
    """Доступ к S3/MinIO. ``public_base_url=None`` → ``{endpoint}/{bucket}`` (path-style)."""

    endpoint: str = _DEF_ENDPOINT
    bucket: str = _DEF_BUCKET
    access_key: str = _DEF_ACCESS_KEY
    secret_key: str = _DEF_SECRET_KEY
    public_base_url: str | None = _DEF_PUBLIC_BASE
    region: str = "us-east-1"


class S3Photos:
    """Переиспользуемый загрузчик фото в S3/MinIO. Один на воркер (boto3-клиент
    thread-safe, строится лениво при первой заливке). ``upload_jpeg`` заливает оригинал
    JPEG и возвращает публичный URL (= ``s3_key`` для БД)."""

    def __init__(self, config: S3Config | None = None):
        self._cfg = config or S3Config()
        self._client = None  # lazy boto3 s3 client

    @property
    def base_url(self) -> str:
        return self._cfg.public_base_url or f"{self._cfg.endpoint}/{self._cfg.bucket}"

    def public_url(self, key: str) -> str:
        return f"{self.base_url}/{key}"

    def _ensure_client(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self._cfg.endpoint,
                aws_access_key_id=self._cfg.access_key,
                aws_secret_access_key=self._cfg.secret_key,
                region_name=self._cfg.region,
                config=_BotoConfig(s3={"addressing_style": "path"}),  # MinIO — path-style
            )
        return self._client

    async def upload_jpeg(self, key: str, content: bytes) -> str:
        """Заливает ``content`` (JPEG как есть) под ключом ``key``; возвращает публичный
        URL. Fail-fast: ошибка boto3 летит наружу."""
        client = self._ensure_client()
        await asyncio.to_thread(
            client.put_object,
            Bucket=self._cfg.bucket, Key=key, Body=content, ContentType="image/jpeg",
        )
        return self.public_url(key)
