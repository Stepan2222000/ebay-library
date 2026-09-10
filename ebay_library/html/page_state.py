"""Детект состояния страницы (Слой 1 — единственный владелец «что за страница»).

Скопировано из старого ``ebaylib`` + порт под in-page fetch (SPEC.md §5.3): для
каталога классифицируем НЕ отрендеренную страницу, а **тело fetch-ответа** — по
``response.url`` + ``<title>``, выдранному из HTML-строки (``detect_state_html``).
DOM-якорей не ждём: тело уже полное.

- ``classify(url)`` — тип страницы строго по URL (home/srp/item/pardon/unknown).
- ``detect_antibot(title)`` — antibot/блок по <title> (pardon/access_denied/error_page).
- ``detect_state(url, title)`` / ``detect_state_html(url, html)`` — состояние.

Политика блоков (реализуется в browser-слое, не здесь):
  PARDON        → reload упавшего URL (гасит challenge), повтор; адаптив ×backoff (§7.3);
  ERROR_PAGE    → критическая ошибка, задача падает (§7.2);
  ACCESS_DENIED → смерть страницы: новая страница в сессии (§7.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit


class PageKind(str, Enum):
    HOME = "home"
    SRP = "srp"
    ITEM = "item"
    PARDON = "pardon"
    UNKNOWN = "unknown"


class Antibot(str, Enum):
    PARDON = "pardon"
    ACCESS_DENIED = "access_denied"
    # Статическая страница "SORRY — Something went wrong on our end"
    # (<title>Error Page | eBay, robots=noindex, reference-id, без JS-челленджа).
    # Типична для холодного захода без прогрева. Критическая (ErrorPageError).
    ERROR_PAGE = "error_page"


def classify(url: str) -> PageKind:
    """Тип страницы строго по URL.

    Pardon виден по пути /splashui/challenge (eBay редиректит туда исходный
    запрос). Access Denied тут не определяется — он на обычном URL, ловится
    через ``detect_antibot`` по <title>.
    """
    parts = urlsplit(url)
    host = parts.netloc.lower()
    path = parts.path or "/"

    if "ebay.com" not in host:
        return PageKind.UNKNOWN
    if path.startswith("/splashui/challenge"):
        return PageKind.PARDON
    if path.startswith("/sch/"):
        return PageKind.SRP
    if path.startswith("/itm/"):
        return PageKind.ITEM
    if path == "/":
        return PageKind.HOME
    return PageKind.UNKNOWN


# Локализованные <title> страницы Pardon Our Interruption (en + 4 локали).
# На американских прокси по умолчанию ловим только английский вариант.
_PARDON_TITLES = (
    "pardon our interruption",     # en
    "desculpe interromper",        # pt-BR
    "disculpe la interrupci",      # es
    "désolé pour l'interruption",  # fr
    "entschuldigen sie die st",    # de
)
_ACCESS_DENIED_TITLES = ("access denied",)
_ERROR_PAGE_TITLES = ("error page | ebay",)


def detect_antibot(title: str | None) -> Antibot | None:
    """Antibot/блок по тексту <title>. ``None`` — нормальная страница.

    Access Denied проверяем первым (блок, лечится заменой страницы), затем
    Error Page (критическая), затем Pardon (мягкий челлендж — ждём). Обработку
    каждого решает browser-слой — здесь только различаем.
    """
    if not title:
        return None
    low = title.lower()
    if any(p in low for p in _ACCESS_DENIED_TITLES):
        return Antibot.ACCESS_DENIED
    if any(p in low for p in _ERROR_PAGE_TITLES):
        return Antibot.ERROR_PAGE
    if any(p in low for p in _PARDON_TITLES):
        return Antibot.PARDON
    return None


@dataclass(frozen=True, slots=True)
class PageState:
    """Состояние страницы: тип (по url) + antibot/блок (по title)."""

    kind: PageKind
    antibot: Antibot | None


def detect_state(url: str, title: str | None) -> PageState:
    """Полное состояние страницы из url + title (без DOM)."""
    return PageState(kind=classify(url), antibot=detect_antibot(title))


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def detect_state_html(response_url: str, html: str) -> PageState:
    """Состояние по СЫРОМУ HTML in-page fetch: ``response.url`` (после редиректов)
    + ``<title>`` из тела. Для каталога (SPEC.md §5.3) — вместо ``detect_state``
    на отрендеренной странице."""
    m = _TITLE_RE.search(html)
    title = m.group(1).strip() if m else None
    return detect_state(response_url, title)


# eBay пишет class без кавычек: <h1 id=srp-results-heading class=srp-controls__count-heading>
_SRP_COUNT_HEADING_RE = re.compile(r"<h1[^>]*\bclass=[\"']?[^\"'>]*srp-controls__count-heading", re.I)


def srp_rendered(html: str) -> bool:
    """Выдача реально отрисована? eBay отдаёт с кодом 200 и обычным <title> «X for sale | eBay»
    серверные заглушки без блока результатов («There seems to be a problem serving the
    request…» или одна шапка сайта). Признак настоящей выдачи, включая 0 results, —
    ``h1.srp-controls__count-heading``. По тексту различать нельзя: фразы ошибок лежат
    в JS-словаре GLOBAL_CONTENT на каждой странице. Транзиент (те же детали в соседние
    дни фетчатся нормально) → повтор fetch в catalog_session, не ParseError."""
    return _SRP_COUNT_HEADING_RE.search(html) is not None


def is_ready(state: PageState, expect: PageKind) -> bool:
    """Страница готова: не antibot и тип совпал с ожидаемым."""
    return state.antibot is None and state.kind is expect
