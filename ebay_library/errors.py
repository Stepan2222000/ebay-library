"""Все исключения библиотеки — одно место.

Философия — specs/architecture.md: обязательное поле не выбили → ParseError с
сырьём наружу (без тихих фолбеков); antibot-блоки → исключения по политикам
из specs/page_readiness.md.
"""

from __future__ import annotations


class ParseError(Exception):
    """Обязательное поле не распарсилось (каталог или item). Несёт сырьё
    (HTML карточки/страницы) для внешнего разбора."""

    def __init__(self, field: str, raw: str | None, entity_id: str | None, html: str):
        super().__init__(f"field '{field}' failed (id={entity_id}, raw={raw!r})")
        self.field = field
        self.raw = raw
        self.entity_id = entity_id  # item_id карточки / item_number товара
        self.html = html


class AccessDeniedError(Exception):
    """Жёсткий блок (Access Denied) — воркер должен помереть."""


class ErrorPageError(Exception):
    """Транзиентный 'Error Page | eBay' — retryable, обработку решает воркер."""
