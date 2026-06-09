"""Модели данных библиотеки (Слой 1). Все поля обязательны — при любой
нестыковке парсер падает с сырьём наружу, а не подставляет None.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SrpCard:
    """Сырая карточка выдачи — что отрисовал eBay (Слой 1, без сети).

    Цена/доставка — в ИСХОДНОЙ валюте листинга (как на сайте), валюта — в
    ``currency_raw`` (токен 'US $'/'C $'/'£'/'EUR'…). Перевод в USD делает
    fx.convert_cards (Слой 2) → CatalogItem. URL не храним — из item_id."""

    item_id: str               # 12 цифр (placeholder "Shop on eBay" отсеян)
    title: str                 # без суффикса "Opens in a new window or tab"
    condition: str             # нормализованное: "new" | "other"
    price: float               # сумма в исходной валюте
    currency_raw: str          # валютный токен как на сайте ('$','US $','C $','EUR'…)
    shipping_cost: float | None  # исходная валюта; 0.0=Free; None="Shipping not specified"
    seller: str
    location: str | None       # из "Located in <...>"; eBay рендерит лениво/не всегда
    image_url: str


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Карточка выдачи с ценой В USD — итог для воркера (Слой 2).

    Получается из SrpCard конвертацией через fx-микросервис. Исходную валюту
    не храним (по контракту — только USD). URL не храним — из item_id."""

    item_id: str            # 12 цифр
    title: str              # без суффикса "Opens in a new window or tab"
    condition: str          # нормализованное: "new" | "other"
    price: float            # в USD
    shipping_cost: float | None  # в USD; 0.0=Free; None="Shipping not specified"
    seller: str
    location: str | None    # из "Located in <...>"; None допустим (lazy-рендер)
    image_url: str


@dataclass(frozen=True, slots=True)
class SearchPage:
    """Результат парсинга одной страницы выдачи (сырьё, до конвертации в USD)."""

    results_count: int            # из заголовка srp-controls__count-heading
    items: list[SrpCard]          # только точные (до сепаратора fewer-words)
    has_fewer_words_sep: bool     # встречен ли сепаратор «Results matching fewer words»


@dataclass(frozen=True, slots=True)
class Catalog:
    """Весь каталог по одной подзадаче (поисковому запросу) — склейка всех
    страниц выдачи (Слой 2)."""

    query: str                    # поисковый запрос подзадачи
    results_count: int            # счётчик с первой страницы
    items: list[CatalogItem]      # все страницы, дедуп по item_id, порядок сохранён
    pages_fetched: int            # сколько страниц реально обошли
    has_fewer_words_sep: bool     # встречался ли сепаратор fewer-words


@dataclass(frozen=True, slots=True)
class CatalogBatch:
    """Результат парсинга блока подзадач. Объединённый список без дублей +
    детали и ошибки по каждой подзадаче (упавшая не валит весь блок —
    оркестратор переотдаст её позже)."""

    items: list[CatalogItem]            # все уникальные карточки (дедуп по item_id)
    per_query: dict[str, Catalog]       # успешно собранные каталоги по запросу
    errors: dict[str, str]              # запрос → текст ошибки (упавшие)


@dataclass(frozen=True, slots=True)
class ItemPage:
    """Страница товара (PDP). Все поля обязательны, кроме last_updated.

    Цена и доставка — всегда в USD (на intl-листингах берётся
    «Approximately US $X»). Тело описания не тянем — только URL iframe.
    """

    item_number: str            # eBay item number (цифры)
    title: str                  # без суффикса "Opens in a new window or tab"
    condition: str              # нормализованное: "new" | "other"
    price_usd: float            # итоговая цена в USD (approx если intl, иначе primary)
    shipping_cost: float        # в USD; 0.0 = Free
    seller: str                 # имя продавца (buybox)
    location: str               # из "Located in: <...>"
    specifics: dict[str, str]   # вся таблица характеристик (key → value)
    image_urls: list[str]       # full-size (data-zoom-src), дедуп
    description: str            # текст из iframe-описания ("" — валидно, если пусто)
    last_updated: str | None    # дата правки листинга, если есть; иначе None
