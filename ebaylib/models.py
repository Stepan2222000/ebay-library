"""Модели данных библиотеки — контракт API (dataclasses, frozen, slots).

Все поля обязательны — при любой нестыковке парсер падает с сырьём наружу,
а не подставляет None. Опциональны только поля, для которых живьём
подтверждено легитимное отсутствие: ``location`` и ``condition`` (SRP) и
``last_updated`` (item).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SrpCard:
    """Сырая карточка выдачи — что отрисовал eBay (Слой 1, без сети).

    Цена/доставка — в ИСХОДНОЙ валюте листинга (как на сайте), валюта — в
    ``currency_raw`` (токен 'US $'/'C $'/'£'/'EUR'…). Перевод в USD делает
    http/fx.convert_cards → CatalogItem. URL не храним — из item_id."""

    item_id: str               # 12 цифр (placeholder "Shop on eBay" отсеян)
    title: str                 # без суффикса "Opens in a new window or tab"
    condition: str | None      # "new" | "other"; None — карточка без состояния (live-кейс)
    price: float               # сумма в исходной валюте
    currency_raw: str          # валютный токен как на сайте ('$','US $','C $','EUR'…)
    shipping_cost: float       # исходная валюта; 0.0 = Free («Shipping not specified» → ParseError)
    seller: str
    location: str | None       # из "Located in <...>"; eBay рендерит лениво/не всегда
    image_url: str


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Карточка выдачи с ценой В USD — итог для воркера.

    Получается из SrpCard конвертацией через fx-микросервис. Исходную валюту
    не храним (по контракту — только USD). URL не храним — из item_id."""

    item_id: str            # 12 цифр
    title: str              # без суффикса "Opens in a new window or tab"
    condition: str | None   # "new" | "other"; None — карточка без состояния
    price: float            # в USD
    shipping_cost: float    # в USD; 0.0 = Free
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
    """Весь каталог по одному поисковому запросу — склейка всех страниц выдачи."""

    query: str                    # поисковый запрос
    results_count: int            # счётчик с первой страницы
    items: list[CatalogItem]      # все страницы, дедуп по item_id, порядок сохранён
    pages_fetched: int            # сколько страниц реально обошли
    has_fewer_words_sep: bool     # встречался ли сепаратор fewer-words


@dataclass(frozen=True, slots=True)
class CatalogResult:
    """Итог ``EbaySession.fetch_catalog`` (один запрос или список запросов).

    Поля ``errors`` нет: любая критическая ошибка валит вызов целиком (без
    частичных результатов), переотдача задачи — забота оркестратора."""

    items: list[CatalogItem]            # все уникальные карточки (глобальный дедуп по item_id)
    per_query: dict[str, Catalog]       # каталог по каждому запросу (в порядке вызова)


@dataclass(frozen=True, slots=True)
class ItemPage:
    """Страница товара (PDP). Все поля обязательны, кроме last_updated.

    Цена и доставка — всегда в USD (на intl-листингах берётся
    «Approximately US $X»). Описание — текст из iframe-описания.
    """

    item_number: str            # eBay item number (цифры)
    title: str                  # без суффикса "Opens in a new window or tab"
    condition: str              # нормализованное: "new" | "other"
    price_usd: float            # итоговая цена в USD (approx если intl, иначе primary)
    shipping_cost: float        # в USD; 0.0 = Free
    seller: str                 # username продавца (как в каталоге)
    location: str               # из "Located in: <...>"
    specifics: dict[str, str]   # вся таблица характеристик (key → value)
    image_urls: list[str]       # full-size (data-zoom-src), дедуп
    description: str            # текст из iframe-описания ("" — валидно, если пусто)
    last_updated: str | None    # дата правки листинга, если есть; иначе None
