"""Скачивание фото товара (CDN ``i.ebayimg.com``).

Не Слой 1 (парсинг) и не Слой 2 (worker-``page``): это чистый HTTP-IO без
браузера. ``i.ebayimg.com`` — раздатчик статики без антибота eBay: тянется
голым GET, без кук/прокси/User-Agent (подтверждено live: 10 мин до 30 потоков,
0 блоков). Поэтому фото качаем напрямую httpx-ом, мимо Playwright.

Вход — ``image_urls`` товара (``ItemPage.image_urls``). На диск НЕ пишем и имена
не придумываем — куда и как класть решает оркестратор.
"""

from __future__ import annotations

import asyncio

import httpx


async def fetch_images(urls: list[str]) -> list[bytes]:
    """Качает фото по списку URL, возвращает байты В ТОМ ЖЕ ПОРЯДКЕ (``result[i]``
    ↔ ``urls[i]``). Один ``AsyncClient`` на пачку (переиспользование соединений),
    без ограничения параллельности. Fail-fast: любой не-2xx или сетевой сбой
    пробрасывается наружу (фото бьются редко, тихих дыр не оставляем)."""
    async with httpx.AsyncClient(timeout=30.0) as client:

        async def one(url: str) -> bytes:
            r = await client.get(url)
            r.raise_for_status()
            return r.content

        return await asyncio.gather(*(one(u) for u in urls))
