"""T5 — парсинг каталога. Прогон: PYTHONPATH=. python3 tests/test_catalog.py"""

from pathlib import Path

from ebaylib import ParseError, parse_search_page

FIX = Path(__file__).parent / "fixtures"


def _check_items(items):
    for it in items:
        assert it.item_id.isdigit() and len(it.item_id) == 12, it.item_id
        assert it.title
        assert it.condition in ("new", "other"), it.condition
        assert it.price > 0, it.price
        assert it.currency_raw, it.item_id  # сырой токен валюты ('$','C $'…)
        assert it.shipping_cost >= 0.0, it.shipping_cost  # обязателен; 0.0 = Free
        # seller — чистый ник, без рейтинга/счётчиков
        assert it.seller, it.item_id
        assert not it.seller.isdigit(), it.seller
        assert "sold" not in it.seller.lower(), it.seller
        assert "%" not in it.seller, it.seller
        # location опционален (eBay рендерит лениво/не всегда)
        assert it.location is None or it.location, it.item_id
        assert it.image_url.startswith("http"), it.image_url


def test_srp_8M6000623():
    html = (FIX / "srp_8M6000623_enUS.html").read_text(encoding="utf-8", errors="replace")
    page = parse_search_page(html)
    assert page.results_count == 273, page.results_count
    assert len(page.items) == 60, len(page.items)
    assert page.has_fewer_words_sep is False
    _check_items(page.items)


def test_srp_3211206():
    # авто-запчасти: subtitle с текстом совместимости ("Replaces OEMs…") и
    # карточки с несколькими .primary.large ("400 sold" перед продавцом).
    html = (FIX / "srp_3211206_enUS.html").read_text(encoding="utf-8", errors="replace")
    page = parse_search_page(html)
    assert page.results_count == 137, page.results_count
    assert len(page.items) == 137, len(page.items)
    _check_items(page.items)
    # ранее ломавшаяся карточка
    broken = [it for it in page.items if it.item_id == "273631183590"]
    assert broken and broken[0].seller == "caltric", broken


def test_srp_8M0142836_no_location():
    # выдача, где eBay не отрендерил "Located in" ни у одной карточки даже при
    # выставленном ZIP=19701 (lazy-подгрузка) — парсер не падает, location=None.
    # Точных результатов 10 (счётчик=10, дальше сепаратор fewer-words).
    html = (FIX / "srp_8M0142836_zip19701.html").read_text(encoding="utf-8", errors="replace")
    page = parse_search_page(html)
    assert page.results_count == 10, page.results_count
    assert page.has_fewer_words_sep is True
    assert len(page.items) == 10, len(page.items)
    _check_items(page.items)
    assert all(it.location is None for it in page.items)


def test_not_srp_raises():
    # caller гарантирует тип через page_state; не-SRP HTML → ParseError
    # (нет счётчика результатов), а не отдельный WrongPageError.
    try:
        parse_search_page("<html><body>nope</body></html>")
    except ParseError:
        pass
    else:
        raise AssertionError("expected ParseError")


def test_convert_cards_live():
    # живой fx-эндпоинт: SrpCard (native + токен) → CatalogItem (USD). Прогон
    # требует сети до fx-сервиса (FX_API_URL). Все карточки фикстуры — '$'/USD,
    # значит price_usd == price native до цента.
    import asyncio

    from ebaylib import convert_cards

    html = (FIX / "srp_8M6000623_enUS.html").read_text(encoding="utf-8", errors="replace")
    cards = parse_search_page(html).items
    items = asyncio.run(convert_cards(cards))
    assert len(items) == len(cards)
    for c, it in zip(cards, items):
        assert it.item_id == c.item_id
        assert not hasattr(it, "currency_raw")  # валюту в итоге не храним
        if c.currency_raw == "$":  # USD → сумма не меняется
            assert it.price == c.price, (c.price, it.price)
        assert it.price > 0


if __name__ == "__main__":
    test_srp_8M6000623()
    test_srp_3211206()
    test_srp_8M0142836_no_location()
    test_not_srp_raises()
    test_convert_cards_live()
    print("PASS")
