"""Конфиг библиотеки — общие параметры парсинга/навигации.

Без сети и сайд-эффектов: только значения, которые потребляют слои 1 и 2.
Воркер может создать свой ``EbayConfig(...)`` с другими значениями.
"""

from __future__ import annotations

from dataclasses import dataclass

# Шаблон URL выдачи. Подставляем артикул (`kw`) и номер страницы (`pgn`).
# Фиксирован: cond=Used (LH_ItemCondition=3), 240 карточек на страницу.
SEARCH_URL_TEMPLATE = (
    "https://www.ebay.com/sch/i.html"
    "?_nkw={kw}&_sacat=0&_from=R40&rt=nc&LH_ItemCondition=3&_ipg={ipg}&_pgn={pgn}"
)


@dataclass(frozen=True, slots=True)
class EbayConfig:
    """Параметры сессии парсинга eBay."""

    zip: str = "19701"
    # Текст опции страны в модале смены ZIP. На eBay у <select> числовые
    # порядковые value — выбираем по label, не по ISO-коду (см. catalog_selectors.md).
    country_option: str = "United States - USA"
    items_per_page: int = 240
    search_url_template: str = SEARCH_URL_TEMPLATE
    pardon_timeout_s: float = 180.0

    def search_url(self, query: str, page: int = 1) -> str:
        """URL выдачи для поискового запроса и номера страницы (1-based)."""
        return self.search_url_template.format(
            kw=query, ipg=self.items_per_page, pgn=page
        )
