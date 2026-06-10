"""Все исключения библиотеки — одно место.

Политика (specs/architecture.md): заменой страницы лечатся ТОЛЬКО блокировка
eBay (``AccessDeniedError``) и транспортная смерть page (net::ERR_*/краш/
закрытие — классифицирует browser/session, это исключения Playwright, не
наши). Всё остальное — критические ошибки: летят наружу и валят задачу и
воркера целиком (``ParseError``, ``ErrorPageError``, таймауты Pardon/якорей/
iframe-описания, сбой fx, неопознанное).

Модуль чистый — без playwright-импортов (Слой 1 работает офлайн).
"""

from __future__ import annotations


class ParseError(Exception):
    """Обязательное поле не распарсилось (каталог или item). Несёт сырьё
    (HTML карточки/страницы) для внешнего разбора. Критическая."""

    def __init__(self, field: str, raw: str | None, entity_id: str | None, html: str):
        super().__init__(f"field '{field}' failed (id={entity_id}, raw={raw!r})")
        self.field = field
        self.raw = raw
        self.entity_id = entity_id  # item_id карточки / item_number товара
        self.html = html


class AccessDeniedError(Exception):
    """Блокировка eBay (Access Denied). Единственная ошибка eBay, которая
    лечится заменой страницы: сессия запрашивает у воркера новый page и
    продолжает с того же места (см. browser/session)."""


class ErrorPageError(Exception):
    """'Error Page | eBay' («SORRY — Something went wrong»). Критическая —
    наружу, задача падает."""
