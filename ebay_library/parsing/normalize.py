"""Общие нормализации (Слой 1) — используются и каталогом, и item-парсером.

Единое место для правил, чтобы логика не дублировалась между catalog.py и
item.py. См. specs/catalog_selectors.md и specs/item_selectors.md.
"""

from __future__ import annotations

# Состояния, нормализуемые в "new". Остальные известные → "other".
NEW_CONDITIONS = {
    "brand new", "new", "new (other)", "new other (see details)", "open box",
}
# Все известные значения состояния. Нужны, чтобы (в каталоге) найти нужный span
# среди нескольких — первым там может стоять текст совместимости, а не состояние.
KNOWN_CONDITIONS = NEW_CONDITIONS | {
    "pre-owned", "used", "for parts or not working",
    "seller refurbished", "certified - refurbished", "excellent - refurbished",
    "very good - refurbished", "good - refurbished",
}


def normalize_condition(raw: str) -> str | None:
    """`raw` состояние → "new" | "other". None, если значение неизвестно
    (caller решает, ошибка это или нет)."""
    low = raw.strip().lower()
    if low not in KNOWN_CONDITIONS:
        return None
    return "new" if low in NEW_CONDITIONS else "other"
