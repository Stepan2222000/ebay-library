"""Слой 1 — парсинг страницы выдачи eBay (SRP). Чистая функция HTML → данные.

Каталог парсим из DOM (SPEC.md §12). Селекторы — selectors.Srp, нормализации —
normalize. Детект «что за страница» сюда НЕ дублируется: caller сперва классифицирует
тело (page_state) и зовёт parse_search_page только для SRP. Здесь — только разбор.

Парсер — **lxml** (`lxml.html` + cssselect), не BeautifulSoup: SRP-страницы огромные
(медиана ~3МБ), bs4(html.parser) парсит ~206мс и держит GIL → потолок ~3.6 стр/сек на
процесс, что упирало throughput каталога. lxml жуёт ту же страницу за ~32мс (~12×).
Эквивалентность bs4↔lxml проверена живьём — 0 расхождений по всем полям на 12607
карточках (этап 9). ``_txt`` мимикрирует ``bs4.get_text(separator, strip=True)``.

Все поля обязательны: любая нестыковка → ParseError наружу (с сырьём), весь парс
страницы падает. «0 results» — это валидный SearchPage(items=[]), не ошибка.
"""

from __future__ import annotations

import re

import lxml.html

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
# Платная доставка: '+<токен><сумма> [слова] delivery/shipping…' — токен любой (его не
# сохраняем; валюта доставки = валюта цены карточки). Между суммой и ключевым словом
# eBay вставляет срок ('+$13.85 next day delivery' — live 2026-07) — допускаем любые
# слова. Ключевое слово обязательно (иначе наивный матч поймал бы 'Free returns' и т.п.).
_PAID_RE = re.compile(
    r"^\+?\s*\D*?(?P<amount>\d[\d,]*(?:\.\d{1,2})?)\s+(?:[\w-]+\s+)*?"
    r"(delivery|shipping|postage|P&P)\b",
    re.I,
)
_SELLER_RE = re.compile(r"^(?P<seller>.+?)\s+\d+(?:\.\d+)?%\s+positive", re.I)
# Строка «про доставку» вообще (для детекта незнакомых формулировок) — по ключевому слову.
_SHIP_KW_RE = re.compile(r"\b(delivery|shipping|postage|P&P)\b", re.I)


def _to_float(amount: str) -> float:
    return float(amount.replace(",", ""))


def _txt(el, sep: str = "") -> str:
    """Текст узла как ``bs4.get_text(separator=sep, strip=True)``: стрипнутые
    непустые фрагменты, склеенные ``sep`` (по умолчанию "" — как у bs4)."""
    if el is None:
        return ""
    return sep.join(s.strip() for s in el.itertext() if s.strip())


def _one(el, sel):
    """``select_one``: первый совпавший элемент или None."""
    found = el.cssselect(sel)
    return found[0] if found else None


def _parse_card(card) -> SrpCard:
    item_id = card.get("data-listingid") or ""
    raw_html = lxml.html.tostring(card, encoding="unicode")

    title = _txt(_one(card, Srp.CARD_TITLE))
    if not title:
        raise ParseError("title", None, item_id, raw_html)
    if title.endswith(_TITLE_SUFFIX):
        title = title[: -len(_TITLE_SUFFIX)].strip()

    # subtitle первым span'ом может содержать не состояние, а текст
    # совместимости ("Replaces OEMs for Polaris…") — ищем известное состояние
    # среди всех span'ов, не только первого. Состояние ОПЦИОНАЛЬНО: у части
    # карточек eBay не рисует его вовсе (подтверждено live 2026-06-10,
    # напр. 400448473243 в выдаче '805079') — None, не ошибка.
    condition = None
    for sp in card.cssselect(Srp.CARD_SUBTITLE_SPANS):
        txt = _txt(sp)
        if txt.lower() in KNOWN_CONDITIONS:
            condition = normalize_condition(txt)
            break

    pe = _one(card, Srp.CARD_PRICE)
    praw = _txt(pe, " ") if pe is not None else None
    if not praw or " to " in praw.lower():
        raise ParseError("price", praw, item_id, raw_html)
    praw_n = re.sub(r"\s+", " ", praw).strip()
    if praw_n.lower() == "see price":
        # MAP-цена («See price»): eBay прячет цену продажи до корзины, в выдаче — только
        # зачёркнутая справочная сумма. Цена карточки = None (решение 2026-09-10; раньше
        # ParseError ронял ВСЮ деталь, 21–29 деталей/день с июля). Валюту для доставки
        # берём из зачёркнутой суммы (в корпусе 29/29 карточек она есть).
        price = None
        currency_raw = None
        st = _one(card, Srp.CARD_PRICE_STRIKE)
        if st is not None:
            sm2 = _PRICE_RE.match(_txt(st, " ").strip())
            if sm2 and sm2.group("cur").strip():
                currency_raw = sm2.group("cur").strip()
    else:
        pm = _PRICE_RE.match(praw_n)
        if not pm:
            raise ParseError("price", praw, item_id, raw_html)
        price = _to_float(pm.group("amount"))
        currency_raw = pm.group("cur").strip()  # сырой токен ('$','US $','C $'…) — переведёт fx
        if not currency_raw:
            raise ParseError("currency", praw, item_id, raw_html)

    # Доставка ОПЦИОНАЛЬНА (None) в двух случаях (корпус 184 прод-падений, 2026-07-16):
    # 1) строка «без суммы» — самовывоз/грузовая/«Delivery or pickup available»
    #    (крупногабарит, 303684073261) / «Shipping not specified» (375075929359):
    #    цену доставки eBay считает при оформлении;
    # 2) строки доставки НЕТ вообще при целых attribute-rows (133/133 в корпусе) —
    #    международные листинги с таможней (import fees): доставка у товара есть
    #    (видна на PDP), но в HTML выдачи eBay её не кладёт. None; авторитет — PDP.
    # ГРЕМИМ (ParseError): незнакомая строка С ключевым словом доставки (новая
    # формулировка eBay) или карточка совсем без attribute-rows (сломанная вёрстка).
    shipping_cost = None
    shipping_matched = False
    rows = [_txt(r, " ") for r in card.cssselect(Srp.CARD_ATTR_ROW)]
    for txt in rows:
        if _FREE_RE.match(txt):
            shipping_cost = 0.0
            shipping_matched = True
            break
        sm = _PAID_RE.match(txt)
        if sm:
            shipping_cost = _to_float(sm.group("amount"))
            shipping_matched = True
            break
    if not shipping_matched:
        unknown_ship_rows = [
            t for t in rows
            if _SHIP_KW_RE.search(t) and not re.match(
                r"^(Shipping not specified|Free local pickup|Freight|"
                r"Delivery or pickup)\b", t, re.I)
        ]
        if unknown_ship_rows or not rows:
            raise ParseError("shipping_cost", None, item_id, raw_html)

    # Продавца якорим по строке "<ник> NN.N% positive" — единственный
    # стабильный признак. Класс .primary.large не уникален (им же помечены
    # "400 sold" и сам рейтинг), поэтому матчим паттерн, а не первый узел.
    # Рейтинг не сохраняем, используем лишь как якорь; ник = до процента.
    seller = None
    for el in (
        card.cssselect(Srp.CARD_SELLER_BADGE)
        + card.cssselect(Srp.CARD_SELLER_PRIMARY)
        + card.cssselect(Srp.CARD_ATTR_ROW)
    ):
        sm = _SELLER_RE.match(_txt(el, " "))
        if sm:
            seller = sm.group("seller")
            break
    # seller=None — штатно (решение зафиксировано, ebay_data миграция 0003). eBay отдаёт
    # два варианта SRP на один и тот же URL/IP/сессию; в варианте с продвигаемыми
    # позициями у части карточек — в т.ч. ОБЫЧНЫХ органических результатов — блок
    # «ник NN% positive» вырезан из HTML, JS его не дорисовывает, маркера в разметке
    # нет (проверено живьём 2026-09-08: 8 повторов, те же item_id то с ником, то без).
    # Выкидывать такие карточки нельзя — это настоящие результаты. БД пишет item с
    # seller_id NULL, авторитетный продавец приходит из PDP (apply_item_snapshot).

    # location опционален: eBay подгружает "Located in" лениво и не всегда
    # (подтверждено live — при выставленном ZIP поле может отсутствовать у всей
    # выдачи). Нет строки → None; точный location берём с item-страницы.
    location = None
    for r in card.cssselect(Srp.CARD_ATTR_ROW):
        txt = _txt(r, " ")
        if txt.lower().startswith("located in"):
            location = txt[len("Located in"):].strip()
            break

    img = _one(card, Srp.CARD_IMG)
    image_url = img.get("src") if img is not None else None
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


def _is_stub_card(li) -> bool:
    """Незаполненный шаблон карточки в первом слоте выдачи (data-view iid:1): заголовок
    пуст, цены нет, внутри только продавец или одно «Sponsored». Пропускаем, а не
    ParseError — иначе теряется вся деталь. На нормальных карточках не срабатывает."""
    t = _one(li, Srp.CARD_TITLE)
    if t is not None and _txt(t):
        return False
    return _one(li, Srp.CARD_PRICE) is None


def parse_search_page(html: str) -> SearchPage:
    """Парсит HTML страницы выдачи. ParseError, если обязательное поле
    (счётчик результатов или поле карточки) не распарсилось. Caller гарантирует,
    что это SRP (проверено page_state)."""
    doc = lxml.html.fromstring(html)
    heading = _one(doc, Srp.COUNT_HEADING)
    results_count = None
    if heading is not None:
        hm = re.match(r"^([\d,]+)", _txt(heading))
        if hm:
            results_count = int(hm.group(1).replace(",", ""))
    if results_count is None:
        raise ParseError("results_count", None, None, html)

    # 0 точных результатов → пустой каталог. eBay при этом часто показывает
    # «похожие» (Results matching fewer words) прямо в .srp-results, а сам
    # сепаратор уносит в отдельный блок srp-river-answer ВНЕ списка li — так
    # что li-цикл его не видит и взял бы похожие за результаты (чужие товары
    # под несуществующий артикул + падёж на рекламных карточках без seller).
    # Поэтому при count==0 карточки НЕ парсим; сепаратор для флага ищем по
    # всему документу. Live 2026-06-13: 43881A8 (0 + 240 похожих, сепаратор
    # в river-answer), 09651605 (совсем пусто, сепаратора нет).
    if results_count == 0:
        sep = any("fewer words" in _txt(s).lower()
                  for s in doc.cssselect(Srp.FEWER_WORDS_SEP))
        return SearchPage(results_count=0, items=[], has_fewer_words_sep=sep)

    has_fewer_words_sep = False
    items: list[SrpCard] = []
    for li in doc.cssselect(Srp.RESULTS_LI):
        sep = _one(li, Srp.FEWER_WORDS_SEP)
        if sep is not None and "fewer words" in _txt(sep).lower():
            has_fewer_words_sep = True
            break
        classes = (li.get("class") or "").split()
        if "s-card" in classes and li.get("data-listingid"):
            if len(li.get("data-listingid") or "") != 12:  # placeholder
                continue
            if _is_stub_card(li):
                continue
            items.append(_parse_card(li))

    return SearchPage(
        results_count=results_count,
        items=items,
        has_fewer_words_sep=has_fewer_words_sep,
    )
