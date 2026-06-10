"""Слой 1 — парсинг страницы товара eBay (PDP). Чистая функция HTML → данные.

Селекторы и нормализации — см. specs/item_selectors.md. Детект «что за
страница» сюда НЕ дублируется: caller сперва прогоняет page_state и зовёт
parse_item_page только для ITEM. Здесь — только разбор.

Все поля обязательны, кроме last_updated: любая нестыковка → ParseError
наружу (с сырьём), весь парс падает. Цена и доставка всегда в USD.

Описание товара живёт в отдельном iframe (itm.ebaydesc.com), которого нет в
основном HTML. Caller (browser-слой) при необходимости достаёт HTML этого
фрейма (frame.content()) и передаёт вторым аргументом ``description_html``.
Передан — извлекаем текст; не передан — description = "".

Здесь же ``ship_to_location`` — сессионный ZIP item-страниц (истина для
верификации, см. specs/item_flow.md).
"""

from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

from ..errors import ParseError
from ..models import ItemPage
from .normalize import normalize_condition
from .selectors import Item as S


_AMOUNT = r"[\d,]+(?:\.\d{1,2})?"
# username продавца — единственное вхождение в embedded-JSON страницы
_SELLER_USERNAME_RE = re.compile(r'"sellerUserName":"([^"]*)"')


def _txt(el) -> str | None:
    return el.get_text(" ", strip=True) if el else None


def _to_float(amount: str) -> float:
    return float(amount.replace(",", ""))


def _extract_description(description_html: str) -> str:
    """Текст описания из HTML iframe-фрейма. "" — валидно (продавец не заполнил)."""
    soup = BeautifulSoup(description_html, "html.parser")
    body = soup.body or soup
    for tag in body(["script", "style"]):
        tag.decompose()
    return body.get_text("\n", strip=True)


def parse_item_page(html: str, description_html: str | None = None) -> ItemPage:
    """Парсит HTML страницы товара. ParseError, если обязательное поле не
    распарсилось. Caller гарантирует, что это PDP (проверено page_state).

    ``description_html`` — HTML iframe-описания (frame.content()); передан —
    извлекаем текст в поле description, не передан — description = ""."""
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one(S.TITLE)
    if not title_el:
        raise ParseError("title", None, None, html)

    item_number = _txt(soup.select_one(S.ITEM_NUMBER))
    if not item_number or not item_number.isdigit():
        raise ParseError("item_number", item_number, None, html)

    title = title_el.get_text(strip=True)
    if not title:
        raise ParseError("title", None, item_number, html)

    condition_raw = _txt(soup.select_one(S.CONDITION))
    condition = normalize_condition(condition_raw) if condition_raw else None
    if not condition:
        raise ParseError("condition", condition_raw, item_number, html)

    # Цена в USD: на intl-листинге берём "Approximately US $X" (.x-price-approx),
    # на US-листинге его нет — берём прямой child .x-price-primary. Суффикс /ea
    # отбрасываем (regex матчит только число).
    approx = _txt(soup.select_one(S.PRICE_APPROX))
    primary_el = soup.select_one(S.PRICE_PRIMARY)
    primary = _txt(primary_el.select_one(S.PRICE_PRIMARY_DIRECT)) if primary_el else None
    price_src = approx or primary
    if not price_src:
        raise ParseError("price_usd", None, item_number, html)
    pm = re.search(_AMOUNT, price_src)
    if not pm:
        raise ParseError("price_usd", price_src, item_number, html)
    price_usd = _to_float(pm.group(0))

    # Доставка в USD: Free → 0.0; intl → "(approx US $X)"; US → "US $X" в начале.
    # Цену выбить обязаны — иначе ParseError (None не ставим).
    ship_raw = _txt(soup.select_one(S.SHIPPING))
    if not ship_raw:
        raise ParseError("shipping_cost", None, item_number, html)
    if re.match(r"^Free\b", ship_raw, re.I):
        shipping_cost = 0.0
    else:
        sm = re.search(
            r"approx\s+US\s?\$\s?(" + _AMOUNT + r")", ship_raw, re.I
        ) or re.match(r"^US\s?\$\s?(" + _AMOUNT + r")", ship_raw, re.I)
        if not sm:
            raise ParseError("shipping_cost", ship_raw, item_number, html)
        shipping_cost = _to_float(sm.group(1))

    # seller — username (как в каталоге), не display-name (видимый текст
    # карточки — витрина, "Florida Tool Shed"). Единый источник — embedded-JSON
    # "sellerUserName": ровно одно вхождение и всегда ник владельца листинга.
    # Проверено live 2026-06-10 на 23 PDP (магазин/без магазина/EU-фикстуры).
    # Ссылки карточки продавца НЕ годятся: формат зависит от наличия магазина
    # (_ssn= / /str/ / /sch/<user>/m.html), слаг /str/ может отличаться от
    # username (avantims ≠ avantimotorsports), а /str/ ловит и ЧУЖИЕ магазины
    # из рекламных блоков (item 376748192596: /str/gpscity).
    sm = _SELLER_USERNAME_RE.search(html)
    seller = sm.group(1) if sm else None
    if not seller:
        raise ParseError("seller", None, item_number, html)

    # Локация: первый видимый SECONDARY-span с "Located in:" (в intl-кейсах
    # такой же класс стоит на конвертации цены — фильтруем по префиксу текста).
    location = None
    for s in soup.select(S.LOCATION_SPANS):
        t = s.get_text(strip=True)
        if t.startswith("Located in:"):
            location = t[len("Located in:"):].strip()
            break
    if not location:
        raise ParseError("location", None, item_number, html)

    specifics: dict[str, str] = {}
    for dl in soup.select(S.SPECIFICS_DL):
        key = _txt(dl.select_one(S.SPECIFICS_KEY))
        value = _txt(dl.select_one(S.SPECIFICS_VALUE))
        if key:
            specifics[key] = value
    if not specifics:
        raise ParseError("specifics", None, item_number, html)

    # Галерея: full-size URL'ы (data-zoom-src). eBay дублирует каждое фото в
    # карусели — дедупим, сохраняя порядок.
    image_urls: list[str] = []
    for img in soup.select(S.IMAGE_ZOOM):
        url = img.get("data-zoom-src")
        if url and url not in image_urls:
            image_urls.append(url)
    if not image_urls:
        raise ParseError("image_urls", None, item_number, html)

    description = _extract_description(description_html) if description_html else ""

    # last_updated — единственное опциональное (есть только у редактированных
    # листингов). Режем префикс "Last updated on" и хвост "View all revisions".
    last_updated = None
    for el in soup.find_all(string=re.compile(r"Last updated on")):
        if el.find_parent("script"):
            continue
        row = el.find_parent(class_=re.compile(r"ux-layout-section__row"))
        text = row.get_text(" ", strip=True) if row else str(el).strip()
        if text.startswith("Last updated on"):
            text = text[len("Last updated on"):].strip()
        text = re.sub(r"\s*(View all revisions\s*)+$", "", text).strip()
        last_updated = text or None
        break

    return ItemPage(
        item_number=item_number,
        title=title,
        condition=condition,
        price_usd=price_usd,
        shipping_cost=shipping_cost,
        seller=seller,
        location=location,
        specifics=specifics,
        image_urls=image_urls,
        description=description,
        last_updated=last_updated,
    )


_SHIP_TO_RE = re.compile(r'"shipToLocation":"([^"]*)"')


def ship_to_location(html: str) -> str:
    """Фактическая ship-to локация item-страницы (``"shipToLocation"`` в HTML).

    eBay держит ship-to локацию контекст-wide и печатает её в HTML каждой
    item-страницы — это истина для верификации ZIP. На ``/itm/`` URL-параметр
    ``_stpos`` игнорируется — установка только сессионная (setter-визит SRP,
    см. browser/session.EbaySession.fetch_item и specs/item_flow.md). Кука
    ``nonsession`` тоже несёт локацию, но подписана и пишется лениво (~5 с) —
    для проверки непригодна.

    После установки — ``"19701,USA"`` (значение URL-encoded, декодируем);
    без — локация по IP-гео (напр. ``"CV470AL"``). Поля нет → ParseError
    (страница не item / вёрстка уехала).
    """
    m = _SHIP_TO_RE.search(html)
    if not m:
        raise ParseError("ship_to_location", None, None, html)
    return urllib.parse.unquote(m.group(1))
