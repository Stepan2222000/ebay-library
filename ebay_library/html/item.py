"""Слой 1 — парсинг страницы товара eBay (PDP) из встроенной JSON-модели.

JSON-first (SPEC.md §4.3): данные берём из объекта ``modules`` встроенной модели
страницы, а ``sellerUserName`` — регексом по полному HTML (он вне ``modules``). Не из
DOM-вёрстки. Строгие пути проверены на 1000 живых листингов (SPEC.md §13); точная
карта путей — examples/README.md (раздел 1). Любая нестыковка обязательного поля →
``ParseError`` наружу (с сырьём): парсер падает громко (SPEC.md §7).

Цену и доставку в Mode 1 НЕ парсим (SPEC.md §4.4) — ``price_usd``/``shipping_cost``
всегда ``None`` (приходят из каталога). Описание берётся отдельным HTTP-запросом
(itm.ebaydesc.com/itmdesc/<id>) и передаётся как ``description_html`` (SPEC.md §4.5).

Caller гарантирует, что это НАСТОЯЩИЙ листинг (не product-hub/ended) — классификация
выше по потоку (SPEC.md §11.1). Здесь — только разбор.

Замечания, пойманные на сверке с БД (этап 1):
- ``JSONLD.product.name`` приходит HTML-кодированным (сущности + теги ``<wbr/>``) —
  чистим через BeautifulSoup ``get_text`` (HTML регексом не парсим). textSpans
  модулей — уже чистый текст.
- В значениях спецификаций бывают спаны-ссылки (с ``action``) — это UI, не данные,
  пропускаем.
- Строку "Condition" в спецификациях (boilerplate-определение, дубль поля
  ``condition``) исключаем — внутренний ключ ``condition``.
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from ..errors import ParseError
from ..models import ItemPage

_MODULES_RE = re.compile(r'"modules"\s*:\s*\{')
_SELLER_RE = re.compile(r'"sellerUserName":"([^"]*)"')
_SIZE_TOKEN_RE = re.compile(r"s-l\d+")          # размер в URL фото CDN → нормализуем к s-l1600
_LOC_PREFIX_RE = re.compile(r"^located in:?\s*", re.I)
_LAST_UPD_RE = re.compile(r"Last updated on\s*(.+?)$")

# schema.org itemCondition → наша модель {new|other|None}
_CONDITION = {"NewCondition": "new"}

# Якорные разделы основной модели товара — есть ТОЛЬКО в ней; у рекламных
# MERCH_PLACEMENT-модулей их нет. По ним выбираем объект "modules" (см. _main_modules).
_ANCHOR_MODULES = ("ITEM_SPEC_SUMMARY", "JSONLD", "TITLE", "PICTURE", "BUY_BOX")


def _balanced(s: str, start: int) -> str | None:
    """Сбалансированный объект ``{...}`` от позиции ``start`` (учёт строк/экранов)."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def _main_modules(html: str) -> dict:
    """Основная модель товара = объект ``"modules":{...}`` с ЯКОРНЫМИ разделами.

    Выбираем по числу якорей (``_ANCHOR_MODULES``), а НЕ по размеру: на impersonate-
    версии страницы рекламный ``MERCH_PLACEMENT``-модуль бывает 136КБ и обгоняет
    модель по длине → выбор «самый длинный» брал рекламу → ParseError. Якоря есть
    только в модели. Проверено живьём 2026-06-22 (659 листингов, 100%).

    ⚠️ ПРЕДВАРИТЕЛЬНО: завязано на ИМЕНА разделов eBay; если их переименуют — score
    станет 0 у всех объектов → ParseError (громко, сразу заметим). На used/вариативных
    листингах и ещё бо́льшем объёме не проверено."""
    best = None
    best_score = 0
    for m in _MODULES_RE.finditer(html):
        obj = _balanced(html, html.index("{", m.start() + 9))
        if not obj:
            continue
        # дешёвый предфильтр по подстроке — не json.loads-им рекламу зря
        score = sum(1 for k in _ANCHOR_MODULES if f'"{k}"' in obj)
        if score <= best_score:
            continue
        try:
            d = json.loads(obj)
        except ValueError:
            continue
        best = d
        best_score = score
    if best is None or best_score == 0:
        raise ParseError("modules", None, None, html)
    return best


def _dig(obj, *keys, field: str, item_number: str | None, html: str):
    """Строгий проход по точному пути; отсутствие ключа/индекса → ParseError(field)."""
    cur = obj
    for k in keys:
        try:
            cur = cur[k]
        except (KeyError, IndexError, TypeError):
            raise ParseError(field, None, item_number, html) from None
    return cur


def _spans(node, *, skip_action: bool = False) -> list[str]:
    """Тексты всех ``TextSpan`` ВНУТРИ узла (строго в пределах его поддерева).

    ``skip_action`` пропускает спаны-ссылки — у них есть ключ ``action`` (это UI,
    напр. «See all condition definitions», не данные значения)."""
    out: list[str] = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("_type") == "TextSpan" and "text" in n:
                if not (skip_action and "action" in n):
                    out.append(n["text"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return out


def _clean(s: str) -> str:
    """HTML-фрагмент (JSONLD-строка: сущности + теги вроде ``<wbr/>``) → чистый текст.

    Через парсер, не регексом (HTML не регулярен) — bs4 ``get_text`` за один проход
    срезает теги и декодирует сущности; согласовано с ``_extract_description``."""
    return BeautifulSoup(str(s), "html.parser").get_text().strip()


def _map_condition(item_condition: str | None) -> str | None:
    if not item_condition:
        return None
    return _CONDITION.get(item_condition.rsplit("/", 1)[-1], "other")


def parse_item_page(html: str, description_html: str | None = None) -> ItemPage:
    """HTML страницы товара (настоящего листинга) → ``ItemPage``.

    Карта путей — в шапке модуля и examples/README.md (раздел 1). ``ParseError`` на
    отсутствии обязательного поля. ``description_html`` (опц.) — сырой HTML описания
    (http/description): передан — извлекаем текст, нет — ``description = ""``."""
    m = _main_modules(html)

    # item_number: ITEM_SPEC_SUMMARY.sections.itemId.dataItems.itemId.textSpans[1] (цифры)
    iid_spans = _dig(m, "ITEM_SPEC_SUMMARY", "sections", "itemId", "dataItems",
                     "itemId", "textSpans", field="item_number", item_number=None, html=html)
    item_number = (str(iid_spans[1].get("text", "")).strip()
                   if isinstance(iid_spans, list) and len(iid_spans) > 1 else None)
    if not item_number or not item_number.isdigit():
        raise ParseError("item_number", item_number, None, html)

    # title: JSONLD.product.name (HTML-кодирован → чистим парсером)
    prod = _dig(m, "JSONLD", "product", field="JSONLD", item_number=item_number, html=html)
    title = _clean(prod.get("name") or "")
    if not title:
        raise ParseError("title", None, item_number, html)

    # condition: JSONLD.offers.itemCondition (NewCondition→new, иное→other, нет→None)
    offers = prod.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    condition = _map_condition((offers or {}).get("itemCondition"))

    # specifics: ABOUT_THIS_ITEM.sections.features.dataItems → {label: значения}.
    # Строку "condition" (boilerplate-определение) исключаем — дубль поля condition.
    data_items = _dig(m, "ABOUT_THIS_ITEM", "sections", "features", "dataItems",
                      field="specifics", item_number=item_number, html=html)
    specifics: dict[str, str] = {}
    if isinstance(data_items, dict):
        for key, v in data_items.items():
            if key == "condition":
                continue
            labels = _spans(v.get("labels"))
            if not labels:
                continue
            specifics[labels[0]] = " ".join(_spans(v.get("values"), skip_action=True)).strip()
    if not specifics:
        raise ParseError("specifics", None, item_number, html)

    # location: SHIPPING_ATF_SECTION_MODULE.sections.shipping.dataItems → спан "Located in:"
    ship_di = _dig(m, "SHIPPING_ATF_SECTION_MODULE", "sections", "shipping", "dataItems",
                   field="location", item_number=item_number, html=html)
    location = None
    for t in _spans(ship_di):
        if t.strip().lower().startswith("located in"):
            location = _LOC_PREFIX_RE.sub("", t.strip()).strip()
            break
    if not location:
        raise ParseError("location", None, item_number, html)

    # seller: "sellerUserName" по полному HTML (вне modules) — username, как в каталоге
    sm = _SELLER_RE.search(html)
    seller = sm.group(1) if sm else None
    if not seller:
        raise ParseError("seller", None, item_number, html)

    # image_urls: PICTURE.mediaList[].image.originalImg.URL (→ s-l1600, дедуп). Нет
    # mediaList → [] (товар реально без фото). Не из JSONLD.image (тот обрезан до 5).
    picture = _dig(m, "PICTURE", field="image_urls", item_number=item_number, html=html)
    media = picture.get("mediaList") if isinstance(picture, dict) else None
    image_urls: list[str] = []
    if media is not None:
        seen: set[str] = set()
        for x in media:
            u = (((x.get("image") or {}).get("originalImg") or {}).get("URL"))
            if u:
                u = _SIZE_TOKEN_RE.sub("s-l1600", u)
                if u not in seen:
                    seen.add(u)
                    image_urls.append(u)
        if media and not image_urls:
            raise ParseError("image_urls", None, item_number, html)

    # last_updated (опционально): ITEM_SPEC_SUMMARY.sections.revisionHistory
    last_updated = None
    rev = (((m.get("ITEM_SPEC_SUMMARY") or {}).get("sections") or {}).get("revisionHistory"))
    if rev:
        upd = _LAST_UPD_RE.search(" ".join(_spans(rev, skip_action=True)))
        if upd:
            last_updated = upd.group(1).strip() or None

    description = _extract_description(description_html) if description_html else ""

    return ItemPage(
        item_number=item_number,
        title=title,
        condition=condition,
        price_usd=None,        # Mode 1: цена из каталога (SPEC.md §4.4)
        shipping_cost=None,    # Mode 1: доставка из каталога (SPEC.md §4.4)
        seller=seller,
        location=location,
        specifics=specifics,
        image_urls=image_urls,
        description=description,
        last_updated=last_updated,
    )


def _extract_description(description_html: str) -> str:
    """Текст описания из сырого HTML (itm.ebaydesc.com/itmdesc). "" — валидно."""
    soup = BeautifulSoup(description_html, "html.parser")
    body = soup.body or soup
    for tag in body(["script", "style"]):
        tag.decompose()
    return body.get_text("\n", strip=True)
