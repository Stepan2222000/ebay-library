"""Все исключения библиотеки — одно место.

Политика (SPEC.md §7): структурные/критические ошибки → воркер умирает громко
(``ParseError``, ``ErrorPageError``, транспортная смерть page, таймауты),
``AccessDeniedError`` — смерть СТРАНИЦЫ (лечится новой страницей в сессии, §7.3),
транзиент — ретрай (§7.1).

Модуль чистый — без playwright-импортов (Слой 1 работает офлайн).
"""

from __future__ import annotations


class ParseError(Exception):
    """Обязательное поле не распарсилось (каталог или item). Несёт сырьё
    (HTML карточки/страницы) для внешнего разбора. Критическая (SPEC.md §7.2)."""

    def __init__(self, field: str, raw: str | None, entity_id: str | None, html: str):
        super().__init__(f"field '{field}' failed (id={entity_id}, raw={raw!r})")
        self.field = field
        self.raw = raw
        self.entity_id = entity_id  # item_id карточки / item_number товара
        self.html = html


class AccessDeniedError(Exception):
    """Блокировка eBay (Access Denied) — смерть СТРАНИЦЫ, не воркера: сессия просит
    у воркера новый page и продолжает (SPEC.md §7.3, каталог-лейн)."""


class ErrorPageError(Exception):
    """'Error Page | eBay' («SORRY — Something went wrong»). Критическая — наружу,
    задача падает (SPEC.md §7.2)."""


class TransportError(Exception):
    """HTTP-транспорт товара исчерпал ретраи транзиента (сеть/таймаут/503) либо вернул
    неожиданный статус. Критическая — воркер умирает (SPEC.md §7.1/§7.2)."""
