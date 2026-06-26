"""Сборка item-лейна (этап 3): один товар → ``ItemPage``. Без пула и без БД (это
этапы 6/5). Оркеструет транспорт (``http/fetch``) и парсинг (``html/item``) в порядке
SPEC.md §4.2: основной запрос → описание (только при успехе основного) → парс.

«По жёсткому» (SPEC.md §7): любой сбой — транспорт (включая сбой запроса описания,
§4.5) или ``ParseError`` — летит наружу и валит задачу. ended/product-hub пока не
классифицируем (добавим позже) — на не-листинге штатно падает ``ParseError``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
from curl_cffi import AsyncSession

from .errors import ParseError, TransportError
from .html.item import _image_urls_from_html, parse_item_page, status_from_html
from .http.fetch import fetch_description, fetch_item, make_item_session
from .models import ItemPage

_log = logging.getLogger("ebay_library")

_EBAYDESC_URL = "https://itm.ebaydesc.com/itm/{item_id}"
# ?nordt=true обходит редирект /itm→/p/ и отдаёт ОРИГИНАЛЬНЫЙ листинг делистнутого товара
_NORDT_URL = "https://www.ebay.com/itm/{item_id}?nordt=true&orig_cvip=true"
_HEADERS = {"Accept-Language": "en-US"}        # обязателен (локаль гео иначе)
_RETRIES = 3
_BACKOFF_S = 0.3


@dataclass(frozen=True, slots=True)
class PhotoResult:
    """Итог ``fetch_image_urls`` по одному ``item_id``: статус + правильные фото.

    ``status`` — "live" | "ended"; ``source`` — откуда взяты фото: "ebaydesc" (листинг,
    live/проданный), "nordt" (delisted — настоящая галерея с основной страницы) или
    "none" (removed/404); ``urls`` — ссылки на фото (s-l1600), ``[]`` если их нет."""

    item_id: str
    status: str
    source: str
    urls: list[str]


async def fetch_item_page(session: AsyncSession, item_id: str, *, prof=None) -> ItemPage:
    """``item_id`` → ``ItemPage`` (Mode 1: без цены/доставки — из каталога, SPEC.md §4.4).

    Порядок (SPEC.md §4.2): основной HTML (``fetch_item``) → описание отдельным
    запросом (``fetch_description``, §4.5) → парс из JSON-модели (§4.3). Сбой любого
    из шагов критичен (по жёсткому) — летит наружу.

    ``prof`` (опц.) — поэтапный профайлер с методом ``switch(name)``: отмечает стадии
    ``fetch`` / ``desc`` / ``parse`` (worker.StageTimer, этап 6)."""
    if prof is not None:
        prof.switch("fetch")
    html = await fetch_item(session, item_id)
    if prof is not None:
        prof.switch("desc")
    description_html = await fetch_description(session, item_id)
    if prof is not None:
        prof.switch("parse")
    return parse_item_page(html, description_html)


async def _get_ebaydesc(item_id: str | int) -> tuple[int, str | None]:
    """GET ``itm.ebaydesc.com/itm/{id}`` → ``(status_code, text)``. ``200`` → текст; ``404``
    → ``(404, None)`` (страницы нет = removed, НЕ ошибка — выше станет статусом); ``503`` /
    сетевой сбой → ретрай; исчерпание / иной статус → ``TransportError``. httpx (ebaydesc
    un-walled, проверено) — в отличие от ``fetch_item`` отдаёт код, чтобы отличить 404."""
    url = _EBAYDESC_URL.format(item_id=item_id)
    last: object = None
    async with httpx.AsyncClient(timeout=20.0, headers=_HEADERS, follow_redirects=True) as cl:
        for attempt in range(_RETRIES):
            try:
                r = await cl.get(url)
            except httpx.HTTPError as e:
                last = e
                await asyncio.sleep(_BACKOFF_S * (attempt + 1))
                continue
            if r.status_code == 200:
                return (200, r.text)
            if r.status_code == 404:
                return (404, None)
            if r.status_code == 503:
                last = f"HTTP 503 {url}"
                await asyncio.sleep(_BACKOFF_S * (attempt + 1))
                continue
            raise TransportError(f"unexpected status {r.status_code} for {url}")
    raise TransportError(f"retries exhausted ({_RETRIES}) for {url}: {last!r}")


async def _nordt_image_urls(item_id: str | int, *, tries: int = 4) -> list[str]:
    """Настоящая галерея ДЕЛИСТНУТОГО листинга через основную страницу (``?nordt=true``
    обходит редирект на ``/p/``). ``www.ebay.com`` — антибот-walled: ``curl_cffi``
    impersonate + ретрай на «Pardon»/Access Denied. Фото отдаём ТОЛЬКО если nordt вернул
    настоящий листинг (есть ``JSONLD.product``); исчерпание попыток → ``TransportError``."""
    s = AsyncSession(impersonate="chrome", timeout=30, headers=_HEADERS)
    url = _NORDT_URL.format(item_id=item_id)
    iid = str(item_id)
    try:
        for attempt in range(tries):
            try:
                r = await s.get(url)
            except Exception as e:                          # сеть/антибот → повтор
                _log.debug("nordt fetch error for %s: %s", iid, e)
                await asyncio.sleep(_BACKOFF_S * (attempt + 2))
                continue
            if "Pardon Our Interruption" in r.text or "Access Denied" in r.text:
                await asyncio.sleep(_BACKOFF_S * (attempt + 2))
                continue
            try:
                _, has_product = status_from_html(r.text, iid)
            except ParseError:
                has_product = False
            if has_product:                                 # настоящий листинг → фото верные
                return _image_urls_from_html(r.text, iid)
            await asyncio.sleep(_BACKOFF_S * (attempt + 2))
        raise TransportError(f"nordt: real listing not obtained for {iid} ({tries} tries)")
    finally:
        await s.close()


async def listing_status(item_id: str | int) -> str:
    """``item_id`` → ``"live"`` | ``"ended"`` (один дешёвый ebaydesc-запрос, без браузера/БД).

    Один сигнал — schema.org ``JSONLD.product.offers.availability``: ``404`` → ended (removed);
    нет ``JSONLD.product`` (delisted-каталог) → ended; ``InStock`` → live; иначе (OutOfStock/
    неизвестное) → ended. Транзиент исчерпан / неожиданный статус → ``TransportError``."""
    code, html = await _get_ebaydesc(item_id)
    if code == 404:
        return "ended"
    status, _ = status_from_html(html, str(item_id))
    return status


async def fetch_image_urls(item_id: str | int) -> PhotoResult:
    """``item_id`` → ``PhotoResult(status, source, urls)`` — правильные фото И статус из
    ОДНОГО разбора ebaydesc (+ nordt только если delisted). Снаружи нужен только ``item_id``.

    Маршрут (один сигнал — ``JSONLD.product``):
    - ebaydesc ``404`` → status=ended (removed), фото нет (``source="none"``);
    - есть ``product`` (live/проданный) → status из ``availability``, фото из **ebaydesc**
      (проверено: совпадают с настоящей галереей);
    - нет ``product`` (delisted-каталог) → status=ended, фото из **nordt** (оригинальная
      галерея; ebaydesc на таких страницах отдаёт ЧУЖИЕ фото).

    ⚠️ Для ad-hoc «фото по id»: ebaydesc-сессия per-call, nordt-сессия (impersonate)
    per-call с ретраем. Массовый обход — отдельная инфраструктура (как воркер)."""
    iid = str(item_id)
    code, html = await _get_ebaydesc(item_id)
    if code == 404:
        return PhotoResult(item_id=iid, status="ended", source="none", urls=[])
    status, has_product = status_from_html(html, iid)
    if has_product:
        return PhotoResult(item_id=iid, status=status, source="ebaydesc",
                           urls=_image_urls_from_html(html, iid))
    urls = await _nordt_image_urls(item_id)                # delisted → реальная галерея
    return PhotoResult(item_id=iid, status="ended", source="nordt", urls=urls)
