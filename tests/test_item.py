"""T7 — парсинг item (PDP). Прогон: PYTHONPATH=. python3 tests/test_item.py"""

from pathlib import Path

from ebaylib import ParseError
from ebaylib.html.item import parse_item_page, ship_to_location

FIX = Path(__file__).parent / "fixtures"

# item_number → (condition, price_usd, shipping_cost, seller_username, last_updated is None?)
EXPECTED = {
    "277574984378": ("new", 47.50, 28.62, "fltoolbox", True),
    "298318213452": ("new", 154.99, 21.16, "etaccessories", True),
    "395640443895": ("new", 133.50, 0.0, "aftermarketparts3", False),
    "116709108878": ("new", 78.75, 107.39, "australianjetskiparts", False),
    "226914621386": ("new", 803.99, 23.25, "bootundmotor", False),
    "356150673416": ("new", 232.62, 2.17, "tata-ca", False),
}


def _check(it):
    assert it.item_number.isdigit(), it.item_number
    assert it.title
    assert it.condition in ("new", "other", None), it.condition  # None — «-- not specified»
    assert it.price_usd > 0, it.price_usd
    # None — продавец не указал доставку («contact seller», суммы на PDP нет)
    assert it.shipping_cost is None or it.shipping_cost >= 0.0, it.shipping_cost
    assert it.seller
    assert it.location
    assert it.specifics
    assert it.image_urls and all(u.startswith("http") for u in it.image_urls)
    assert isinstance(it.description, str)  # "" валидно (фикстуры без iframe-html)


def test_items():
    for num, (cond, price, ship, seller, lu_none) in EXPECTED.items():
        html = (FIX / f"item_{num}.html").read_text(encoding="utf-8", errors="replace")
        it = parse_item_page(html)
        _check(it)
        assert it.item_number == num, it.item_number
        assert it.condition == cond, (num, it.condition)
        assert abs(it.price_usd - price) < 0.01, (num, it.price_usd)
        assert abs(it.shipping_cost - ship) < 0.01, (num, it.shipping_cost)
        assert it.seller == seller, (num, it.seller)
        assert (it.last_updated is None) == lu_none, (num, it.last_updated)


def test_description_from_iframe_html():
    main = (FIX / "item_277574984378.html").read_text(encoding="utf-8", errors="replace")
    # без второго аргумента — описание пустое
    assert parse_item_page(main).description == ""
    # с переданным iframe-html — извлекаем текст
    desc_html = "<html><body><p>Professionally packaged</p><script>x()</script></body></html>"
    it = parse_item_page(main, desc_html)
    assert it.description == "Professionally packaged", repr(it.description)


def test_local_pickup():
    # самовывоз: строки доставки нет, есть "Pickup: Local pickup only from…" →
    # shipping None; «Located in:» на такой странице нет — локация из pickup-строки.
    html = (FIX / "item_121427597766_pickup.html").read_text(encoding="utf-8", errors="replace")
    it = parse_item_page(html)
    _check(it)
    assert it.shipping_cost is None, it.shipping_cost
    assert it.condition == "other" and it.seller == "alecotooling"
    assert it.location == "Milwaukee, Wisconsin, United States 53209", it.location


def test_condition_dash_none():
    # продавец не указал состояние: блок рендерит "-- not specified" → None
    html = (FIX / "item_324023434393_nocond.html").read_text(encoding="utf-8", errors="replace")
    it = parse_item_page(html)
    _check(it)
    assert it.condition is None, it.condition
    assert it.seller == "brozius"


def test_not_item_raises():
    # caller гарантирует тип через page_state; если всё же подан не-item HTML —
    # обязательное поле не найдётся → ParseError (не отдельный WrongPageError).
    try:
        parse_item_page("<html><body>nope</body></html>")
    except ParseError:
        pass
    else:
        raise AssertionError("expected ParseError")


def test_ship_to_location():
    # фикстуры сняты с EU-сессий — локации там EU-форматов (не "{zip},USA");
    # извлечение поля от формата не зависит.
    html = (FIX / "item_277574984378.html").read_text(encoding="utf-8", errors="replace")
    assert ship_to_location(html) == "00-001", ship_to_location(html)
    html = (FIX / "item_116709108878.html").read_text(encoding="utf-8", errors="replace")
    assert ship_to_location(html) == "8000", ship_to_location(html)
    try:
        ship_to_location("<html><body>nope</body></html>")
    except ParseError:
        pass
    else:
        raise AssertionError("expected ParseError")


if __name__ == "__main__":
    test_items()
    test_description_from_iframe_html()
    test_local_pickup()
    test_condition_dash_none()
    test_not_item_raises()
    test_ship_to_location()
    print("PASS")
