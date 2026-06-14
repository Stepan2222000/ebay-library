"""Описание товара с CDN-хоста описаний (``itm.ebaydesc.com``).

Не Слой 1 (парсинг) и не Слой 2 (worker-``page``): чистый HTTP-IO без браузера.
Описание продавца — отдельный документ, адресуемый ТОЛЬКО по item_id:
``itm.ebaydesc.com/itmdesc/{item_id}`` (страница товара лишь вставляет его
iframe'ом на тот же URL). Сервер отдаёт готовый HTML описания целиком по
голому GET — без параметров, кук, прокси и User-Agent. Это статик-хост без
антибота (live: 1000 GET за 11с с одного IP — 0 блоков), как ``i.ebayimg.com``
для фото. Поэтому описание тянем напрямую httpx-ом, мимо Playwright: в браузере
тот же iframe рендерится лениво и эрратично (JS не дорисовывает текст / iframe
вовсе отсутствует — товар при этом описание имеет).

Возвращаем СЫРОЙ HTML — выемку текста делает ``html.item._extract_description``
(Слой 1) внутри ``parse_item_page``. Несуществующий/пустой item_id → сервер
всё равно отдаёт 200 с почти пустым телом → текст "" (не ошибка).
"""

from __future__ import annotations

import httpx

_DESC_URL = "https://itm.ebaydesc.com/itmdesc/{item_id}"
_TIMEOUT = 30.0


async def fetch_description(item_id: str) -> str:
    """Сырой HTML описания продавца по item_id (для ``parse_item_page``).

    Голый GET к ``itm.ebaydesc.com``; fail-fast: не-2xx или сетевой сбой
    пробрасывается наружу (хост практически не падает, тихих дыр не оставляем)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(_DESC_URL.format(item_id=item_id))
        r.raise_for_status()
        return r.text
