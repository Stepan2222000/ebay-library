"""Единственное место со всеми CSS-селекторами eBay (DRY).

Парсеры (catalog.py, item.py) и логика готовности (navigation.py) берут
селекторы только отсюда — чтобы при изменении вёрстки eBay править одну точку.
Группы: SRP (каталог), ITEM (страница товара). Обоснования — в
specs/catalog_selectors.md и specs/item_selectors.md.
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


# --- Страница товара (ITEM / PDP) ------------------------------------------
class Item:
    TITLE = (
        '[data-ebay-critical-module="TITLE"] '
        "h1.x-item-title__mainTitle span.ux-textspans"
    )
    ITEM_NUMBER = (
        '[data-testid="d-item-details-tab-header"] '
        ".ux-layout-section__textual-display--itemId span.ux-textspans--BOLD"
    )
    PRICE_PRIMARY = ".x-price-primary"
    PRICE_PRIMARY_DIRECT = ":scope > span.ux-textspans"
    PRICE_APPROX = ".x-price-approx .x-price-approx__price"
    CONDITION = '[data-testid="x-item-condition"] .x-item-condition-text .ux-textspans'
    SHIPPING = (
        '[data-ebay-critical-module="SHIPPING_ATF_SECTION_MODULE"] '
        ".ux-labels-values--shipping .ux-labels-values__values-content > div"
    )
    # самовывоз: вместо строки доставки — "Pickup: Local pickup only from <город>"
    # (live 2026-06-12, напр. 121427597766; модификатора --shipping у строки нет)
    PICKUP = (
        '[data-ebay-critical-module="SHIPPING_ATF_SECTION_MODULE"] '
        ".ux-labels-values--localPickup .ux-labels-values__values-content > div"
    )
    SELLER_CARD = ".x-sellercard-atf"  # якорь готовности; ник — из embedded-JSON "sellerUserName"
    LOCATION_SPANS = "span.ux-textspans--SECONDARY"
    SPECIFICS_DL = "dl.ux-labels-values"
    SPECIFICS_KEY = "dt .ux-textspans"
    SPECIFICS_VALUE = "dd .ux-textspans"
    # все img карусели: URL в src (ленивые — в data-src); большую версию строим
    # заменой токена размера на s-l1600 (data-zoom-src бывает пустым)
    IMAGE_CAROUSEL = ".ux-image-carousel-item img"
    DESC_IFRAME = "iframe#desc_ifr"


# ZIP доставки больше не ставится через UI — он задаётся параметром URL
# (`_stpos`, см. config.build_search_url). Селекторов ZIP-флоу нет.


# --- Главная (HOME) --------------------------------------------------------
class Home:
    SEARCH_BOX = "input#gh-ac"
