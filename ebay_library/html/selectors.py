"""Единственное место с CSS-селекторами eBay (DRY).

В v1 нужны только **каталог (SRP)** и **главная (warmup)** — товар парсится
JSON-first (html/item.py), DOM-селекторов товара тут нет. Обоснования селекторов —
старый ``ebaylib/html/selectors.py`` (тест-оракул) и SPEC.md §5.3.
"""

from __future__ import annotations


# --- Каталог (SRP) ---------------------------------------------------------
class Srp:
    COUNT_HEADING = "h1.srp-controls__count-heading"
    RESULTS_LI = ".srp-results > li"
    FEWER_WORDS_SEP = "section.su-notice span.BOLD"

    CARD = "li.s-card[data-listingid]"          # карточка (готовность)
    CARD_TITLE = ".s-card__title"
    CARD_SUBTITLE_SPANS = ".s-card__subtitle .su-styled-text"
    CARD_PRICE = ".s-card__price"
    CARD_ATTR_ROW = ".s-card__attribute-row"
    CARD_IMG = "img.s-card__image"
    # продавца якорим по строке "% positive" среди этих кандидатов
    CARD_SELLER_BADGE = ".s-card__program-badge-container--sellerOrStoreInfo .su-styled-text"
    CARD_SELLER_PRIMARY = ".su-card-container__attributes .su-styled-text.primary.large"


# --- Главная (HOME, прогрев сессии) ----------------------------------------
class Home:
    SEARCH_BOX = "input#gh-ac"
