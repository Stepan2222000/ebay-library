"""Общие нормализации (Слой 1). В v1 — нормализация состояния для карточек выдачи
(каталог). Состояние товара (PDP) берётся из JSON (html/item.py) отдельной картой.
"""

from __future__ import annotations

import re

# Состояние → "new", если в тексте есть слово "new" (граница слова, чтобы
# 'renewed'/'refurbished' не зацепить). eBay плодит new-варианты по категориям
# ('New', 'Brand New', 'New with tags', 'New without tags', 'New with box',
# 'New: Other (See Details)'…) — перечислять их бессмысленно, ищем слово.
_NEW_WORD_RE = re.compile(r"\bnew\b")
# Новое БЕЗ слова "new" — единственный такой вариант (+ написания с тире).
_NEW_EXTRA = {"open box", "open-box"}

# Все известные значения состояния. Нужны КАТАЛОГУ (SRP), чтобы среди
# subtitle-span'ов найти именно состояние: там рядом текст совместимости с
# брендами ('New Holland', 'New Balance') — по слову "new" нельзя, нужен
# точный whitelist.
_NEW_CONDITIONS = {
    "brand new", "new", "new (other)", "new other (see details)", "open box",
    "new – open box", "new - open box",
    "new with tags", "new without tags", "new: other (see details)",
}
KNOWN_CONDITIONS = _NEW_CONDITIONS | {
    "pre-owned", "used", "for parts or not working",
    "seller refurbished", "certified - refurbished", "excellent - refurbished",
    "very good - refurbished", "good - refurbished",
}


def normalize_condition(raw: str) -> str | None:
    """`raw` состояние → "new" | "other". None, если значение неизвестно
    (caller решает, ошибка это или нет).

    "new" — по СЛОВУ "new" в тексте (любой new-вариант) либо "open box";
    остальные известные значения → "other"; всё прочее → None."""
    low = raw.strip().lower()
    if _NEW_WORD_RE.search(low) or low in _NEW_EXTRA:
        return "new"
    if low in KNOWN_CONDITIONS:
        return "other"
    return None
