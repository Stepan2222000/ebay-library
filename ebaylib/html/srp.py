"""Слой 1 — парсинг страницы выдачи eBay (SRP). Чистая функция HTML → данные.

Селекторы и нормализации — см. specs/catalog_selectors.md. Детект «что за
страница» сюда НЕ дублируется: caller сперва прогоняет page_state и зовёт
parse_search_page только для SRP. Здесь — только разбор результата.

Все поля обязательны: любая нестыовка → ParseError наружу (с сырьём), весь
парс страницы падает. «0 results» — это валидный SearchPage(items=[]), не ошибка.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..errors import ParseError
from ..models import SearchPage, SrpCard
from .normalize import KNOWN_CONDITIONS, normalize_condition
from .selectors import Srp


_TITLE_SUFFIX = "Opens in a new window or tab"

# Цену НЕ маппим в код валюты — ловим ЛЮБОЙ валютный токен (всё до первой
# цифры) + сумму в US-формате (запятая=тысячи, точка=десятич; eBay на .com так
# печатает все валюты, в т.ч. "EUR 19.99"/"C $20.55"). Сам токен ('$','US $',
# 'C $','£','EUR'…) резолвит fx-микросервис при конвертации — единый источник
# истины написаний (fx.currency_aliases).
_PRICE_RE = re.compile(r"^(?P<cur>\D*?)(?P<amount>\d[\d,]*(?:\.\d{1,2})?)")
_FREE_RE = re.compile(r"^Free\b.*\b(delivery|shipping|postage|P&P)\b", re.I)
# Платная доставка: '+<токен><сумма> delivery/shipping…' — токен любой (его не
# сохраняем; валюта доставки = валюта цены карточки). Ключевое слово обязательно
# (иначе наивный матч поймал бы 'Free returns'/'30 days' и т.п.).
_PAID_RE = re.compile(
    r"^\+?\s*\D*?(?P<amount>\d[\d,]*(?:\.\d{1,2})?)\s+(delivery|shipping|postage|P&P)\b",
    re.I,
)
_SELLER_RE = re.compile(r"^(?P<seller>.+?)\s+\d+(?:\.\d+)?%\s+positive", re.I)


def _to_float(amount: str) -> float:
    return float(amount.replace(",", ""))


def _parse_card(card) -> SrpCard:
    item_id = card.get("data-listingid", "")
    raw_html = str(card)

    t = card.select_one(Srp.CARD_TITLE)
    if not t or not t.get_text(strip=True):
        raise ParseError("title", None, item_id, raw_html)
    title = t.get_text(strip=True)
    if title.endswith(_TITLE_SUFFIX):
        title = title[: -len(_TITLE_SUFFIX)].strip()

    # subtitle первым span'ом может содержать не состояние, а текст
    # совместимости ("Replaces OEMs for Polaris…") — ищем известное состояние
    # среди всех span'ов, не только первого.
    condition_raw = None
    for sp in card.select(Srp.CARD_SUBTITLE_SPANS):
        txt = sp.get_text(strip=True)
        if txt.lower() in KNOWN_CONDITIONS:
            condition_raw = txt
            break
    if condition_raw is None:
        raise ParseError("condition", None, item_id, raw_html)
    condition = normalize_condition(condition_raw)

    pe = card.select_one(Srp.CARD_PRICE)
    praw = pe.get_text(" ", strip=True) if pe else None
    if not praw or " to " in praw.lower():
        raise ParseError("price", praw, item_id, raw_html)
    pm = _PRICE_RE.match(re.sub(r"\s+", " ", praw).strip())
    if not pm:
        raise ParseError("price", praw, item_id, raw_html)
    price = _to_float(pm.group("amount"))
    currency_raw = pm.group("cur").strip()  # сырой токен ('$','US $','C $'…) — переведёт fx
    if not currency_raw:
        raise ParseError("currency", praw, item_id, raw_html)

    shipping_cost = None
    for r in card.select(Srp.CARD_ATTR_ROW):
        txt = r.get_text(" ", strip=True)
        if _FREE_RE.match(txt):
            shipping_cost = 0.0
            break
        sm = _PAID_RE.match(txt)
        if sm:
            shipping_cost = _to_float(sm.group("amount"))
            break
    if shipping_cost is None:
        raise ParseError("shipping_cost", None, item_id, raw_html)

    # Продавца якорим по строке "<ник> NN.N% positive" — единственный
    # стабильный признак. Класс .primary.large не уникален (им же помечены
    # "400 sold" и сам рейтинг), поэтому матчим паттерн, а не первый узел.
    # Рейтинг не сохраняем, используем лишь как якорь; ник = до процента.
    seller = None
    for el in (
        card.select(Srp.CARD_SELLER_BADGE)
        + card.select(Srp.CARD_SELLER_PRIMARY)
        + card.select(Srp.CARD_ATTR_ROW)
    ):
        sm = _SELLER_RE.match(el.get_text(" ", strip=True))
        if sm:
            seller = sm.group("seller")
            break
    if not seller:
        raise ParseError("seller", None, item_id, raw_html)

    # location опционален: eBay подгружает "Located in" лениво и не всегда
    # (подтверждено live — при выставленном ZIP поле может отсутствовать у всей
    # выдачи). Нет строки → None; точный location берём с item-страницы.
    location = None
    for r in card.select(Srp.CARD_ATTR_ROW):
        txt = r.get_text(" ", strip=True)
        if txt.lower().startswith("located in"):
            location = txt[len("Located in"):].strip()
            break

    img = card.select_one(Srp.CARD_IMG)
    image_url = img.get("src") if img else None
    if not image_url:
        raise ParseError("image_url", None, item_id, raw_html)

    return SrpCard(
        item_id=item_id,
        title=title,
        condition=condition,
        price=price,
        currency_raw=currency_raw,
        shipping_cost=shipping_cost,
        seller=seller,
        location=location,
        image_url=image_url,
    )


def parse_search_page(html: str) -> SearchPage:
    """Парсит HTML страницы выдачи. ParseError, если обязательное поле
    (счётчик результатов или поле карточки) не распарсилось. Caller гарантирует,
    что это SRP (проверено page_state)."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one(Srp.COUNT_HEADING)
    results_count = None
    if heading:
        hm = re.match(r"^([\d,]+)", heading.get_text(strip=True))
        if hm:
            results_count = int(hm.group(1).replace(",", ""))
    if results_count is None:
        raise ParseError("results_count", None, None, html)

    has_fewer_words_sep = False
    items: list[SrpCard] = []
    for li in soup.select(Srp.RESULTS_LI):
        sep = li.select_one(Srp.FEWER_WORDS_SEP)
        if sep and "fewer words" in sep.get_text().lower():
            has_fewer_words_sep = True
            break
        if "s-card" in li.get("class", []) and li.get("data-listingid"):
            if len(li.get("data-listingid", "")) != 12:  # placeholder
                continue
            items.append(_parse_card(li))

    return SearchPage(
        results_count=results_count,
        items=items,
        has_fewer_words_sep=has_fewer_words_sep,
    )
