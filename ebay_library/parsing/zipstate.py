"""Слой 1 — сессионный ZIP доставки для item-страниц. Чистая функция, без браузера.

eBay держит ship-to локацию контекст-wide и печатает её в HTML каждой
item-страницы как ``"shipToLocation"`` — это истина для верификации ZIP.
На ``/itm/`` URL-параметр ``_stpos`` игнорируется — установка возможна только
сессионно (setter-визит SRP, см. worker/fetch.fetch_item и specs/item_flow.md).
Кука ``nonsession`` тоже несёт локацию, но подписана и пишется лениво (~5 c
после страницы) — для проверки непригодна (разбор — specs/item_flow.md).
"""

from __future__ import annotations

import re
import urllib.parse

from ..errors import ParseError

_SHIP_TO_RE = re.compile(r'"shipToLocation":"([^"]*)"')


def ship_to_location(html: str) -> str:
    """Фактическая ship-to локация item-страницы (``"shipToLocation"``).

    После сессионной установки — ``"19701,USA"`` (значение URL-encoded в HTML,
    декодируем); без установки eBay подставляет локацию по IP-гео (напр.
    ``"CV470AL"``). Поля нет → ParseError (страница не item / вёрстка уехала).
    """
    m = _SHIP_TO_RE.search(html)
    if not m:
        raise ParseError("ship_to_location", None, None, html)
    return urllib.parse.unquote(m.group(1))
