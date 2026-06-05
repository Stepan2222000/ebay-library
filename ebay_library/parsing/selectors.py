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
    SELLER_CARD = ".x-sellercard-atf"
    SELLER_LINKS = ".x-sellercard-atf a[href]"
    LOCATION_SPANS = "span.ux-textspans--SECONDARY"
    SPECIFICS_DL = "dl.ux-labels-values"
    SPECIFICS_KEY = "dt .ux-textspans"
    SPECIFICS_VALUE = "dd .ux-textspans"
    IMAGE_ZOOM = ".ux-image-carousel-item img[data-zoom-src]"
    DESC_IFRAME = "iframe#desc_ifr"


# --- Смена ZIP на SRP ------------------------------------------------------
# eBay отдаёт ДВА варианта вёрстки SRP (sticky на контекст): spotlight (наш) и
# A/B. У них разные селекторы ZIP-флоу — set_zip определяет вариант и берёт
# нужный набор. Формат label страны общий: "United States - USA".
class ZipSpotlight:
    TRIGGER = ".x-refine-shipping-spotlight button.s-zipcode-entry__btn"
    LABEL = ".x-refine-shipping-spotlight .s-zipcode-entry__label"
    COUNTRY = ".s-zipcode-entry__modal--delivery select"
    ZIP_INPUT = ".s-zipcode-entry__modal--delivery input[type='text']"
    APPLY = ".s-zipcode-entry__apply button.btn--primary"


class ZipAb:
    TRIGGER = "button.shipping-entry"
    COUNTRY = "div[role=dialog] select"          # выбираем по label среди видимых
    ZIP_INPUT = "input[name='_stpos']"
    APPLY = "div[role=dialog] button.btn--primary"
    # ZIP читается из текста триггера: "Update your location\nShipping to 19701"


# --- Главная (HOME) --------------------------------------------------------
class Home:
    SEARCH_BOX = "input#gh-ac"
