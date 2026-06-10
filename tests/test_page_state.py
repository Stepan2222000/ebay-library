"""T2 — детект страницы и antibot. Прогон: python3 -m pytest или прямой запуск."""

from ebaylib import classify, detect_antibot, PageKind, Antibot

CLASSIFY_CASES = [
    ("https://www.ebay.com/", PageKind.HOME),
    ("https://www.ebay.com/sch/i.html?_nkw=8M6000623&_ipg=60", PageKind.SRP),
    ("https://www.ebay.com/itm/277574984378", PageKind.ITEM),
    ("https://www.ebay.com/splashui/challenge?ap=1&ru=x", PageKind.PARDON),
    ("https://www.ebay.com/b/Boat-Parts/26429/bn_661875", PageKind.UNKNOWN),
    ("https://www.google.com/", PageKind.UNKNOWN),
]

ANTIBOT_CASES = [
    ("8m6000623 for sale | eBay", None),
    ("Electronics, Cars, Fashion, Collectibles & More | eBay", None),
    ("Pardon Our Interruption...", Antibot.PARDON),
    ("Access Denied", Antibot.ACCESS_DENIED),
    ("Error Page | eBay", Antibot.ERROR_PAGE),
    (None, None),
]


def test_classify():
    for url, exp in CLASSIFY_CASES:
        assert classify(url) is exp, url


def test_detect_antibot():
    for title, exp in ANTIBOT_CASES:
        assert detect_antibot(title) is exp, title


if __name__ == "__main__":
    test_classify()
    test_detect_antibot()
    print("PASS")
