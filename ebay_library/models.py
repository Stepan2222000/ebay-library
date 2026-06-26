"""Модели данных библиотеки — контракт API (dataclasses, frozen, slots).

Все поля обязательны — при любой нестыковке парсер падает с сырьём наружу
(``ParseError``), а не подставляет None. Опциональны только поля, для которых
живьём подтверждено легитимное отсутствие: для карточки выдачи — ``location``,
``condition``, ``shipping_cost``; для товара — ``condition``, ``last_updated``, а
также ``price_usd``/``shipping_cost`` (в Mode 1 их не парсим — приходят из каталога,
SPEC.md §4.4).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SrpCard:
    """Сырая карточка выдачи — что отрисовал eBay (Слой 1, без сети).

    Цена/доставка — в ИСХОДНОЙ валюте листинга (как на сайте), валюта — в
    ``currency_raw`` (токен 'US $'/'C $'/'£'/'EUR'…). Перевод в USD делает
    http/fx.convert_cards → CatalogItem (SPEC.md §5.4). URL не храним — из item_id."""

    item_id: str               # 12 цифр (placeholder "Shop on eBay" отсеян)
    title: str                 # без суффикса "Opens in a new window or tab"
    condition: str | None      # "new" | "other"; None — карточка без состояния (live-кейс)
    price: float               # сумма в исходной валюте
    currency_raw: str          # валютный токен как на сайте ('$','US $','C $','EUR'…)
    shipping_cost: float | None  # исходная валюта; 0.0 = Free; None = not specified/самовывоз/freight
    seller: str | None         # ⚠️⚠️ ВРЕМЕННО None (рекламные карточки без seller в сыром HTML — srp.py); ПОД БОЛЬШИМ ВОПРОСОМ
    location: str | None       # из "Located in <...>"; eBay рендерит лениво/не всегда
    image_url: str


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Карточка выдачи с ценой В USD — итог для воркера.

    Получается из SrpCard конвертацией через fx-микросервис (SPEC.md §5.4). Исходную
    валюту не храним (по контракту — только USD). URL не храним — из item_id."""

    item_id: str            # 12 цифр
    title: str              # без суффикса "Opens in a new window or tab"
    condition: str | None   # "new" | "other"; None — карточка без состояния
    price: float            # в USD
    shipping_cost: float | None  # в USD; 0.0 = Free; None = не указана/самовывоз/freight
    seller: str | None      # ⚠️⚠️ ВРЕМЕННО None (рекламные карточки — srp.py); ПОД БОЛЬШИМ ВОПРОСОМ
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
    """Итог обхода каталога (один запрос или список запросов).

    Поля ``errors`` нет: любая критическая ошибка валит вызов целиком (без
    частичных результатов), переотдача задачи — забота оркестратора."""

    items: list[CatalogItem]            # все уникальные карточки (глобальный дедуп по item_id)
    per_query: dict[str, Catalog]       # каталог по каждому запросу (в порядке вызова)


@dataclass(frozen=True, slots=True)
class ItemEnded:
    """Листинг завершён/недоступен (ended, 404, product-hub — SPEC.md §7.5/§11.1):
    данных листинга нет, несём только id. Пишется в БД как смерть (``apply_item_ended``)."""

    item_number: str            # = запрошенный item_id (со страницы не парсим)


@dataclass(frozen=True, slots=True)
class ItemPage:
    """Страница товара (PDP), распарсенная JSON-first (SPEC.md §4.3).

    Обязательны: ``item_number``, ``title``, ``seller``.
    Опциональны (SPEC.md §7.4): ``location`` (None — pickup-only листинг без доставки
    либо eBay не отдал "Located in:"), ``specifics`` ({} — продавец не задал своих
    характеристик; в блоке только Condition+Category), ``condition`` (None — состояние не указано),
    ``last_updated`` (None — листинг не редактировали), ``image_urls`` ([] — у
    листинга нет фото). ``price_usd`` и ``shipping_cost`` в **Mode 1 всегда None** —
    не парсим, приходят из каталога (SPEC.md §4.4); заполняются только в Mode 2 (§9).
    ``description`` — текст с itm.ebaydesc.com ("" — валидно, если пусто)."""

    item_number: str            # eBay item number (цифры)
    title: str                  # чистый (HTML-сущности/теги JSONLD раскодированы)
    condition: str | None       # "new" | "other"; None — состояние не указано
    price_usd: float | None     # Mode 1: None (из каталога); Mode 2: USD
    shipping_cost: float | None  # Mode 1: None (из каталога); Mode 2: USD (0.0 = Free)
    seller: str                 # username продавца (как в каталоге)
    location: str | None        # из "Located in: <...>"; None — pickup-only/eBay не отдал
    specifics: dict[str, str]   # характеристики (key → value); без boilerplate-Condition; {} — продавец не задал
    image_urls: list[str]       # большие версии фото (s-l1600), дедуп; [] — фото нет
    description: str            # текст описания ("" — валидно, если пусто)
    last_updated: str | None    # дата правки листинга, если есть; иначе None
