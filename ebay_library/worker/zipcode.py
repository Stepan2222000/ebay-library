"""Слой 2 — смена ZIP (адреса доставки) на странице выдачи.

ZIP — кука контекста: ставим один раз на сессию, последующие SRP наследуют.
Доставка в карточках зависит от ZIP (без него — "Shipping not specified").

eBay отдаёт ДВА варианта вёрстки SRP, sticky на контекст (подтверждено live:
в одном контексте вариант не меняется между страницами): spotlight (наш) и
A/B. Селекторы каждого — в selectors.py. set_zip определяет вариант и идёт
единым флоу. После Apply сверяем результат по факту; не совпало → ошибка.

Селекторы и нюанс выбора страны (по label, не по ISO) — см.
specs/catalog_selectors.md, раздел «UI Shipping to».
"""

from __future__ import annotations

import asyncio
import re

from ..config import EbayConfig
from ..errors import ZipChangeError
from ..parsing.selectors import ZipAb, ZipSpotlight


async def _detect_variant(page, timeout_ms: int):
    """Ждёт появления любого из двух триггеров и возвращает его набор селекторов.
    Вариант sticky на контекст — определяем один раз за вызов."""
    await page.wait_for_selector(
        f"{ZipSpotlight.TRIGGER}, {ZipAb.TRIGGER}", state="attached", timeout=timeout_ms
    )
    if await page.locator(ZipSpotlight.TRIGGER).count():
        return ZipSpotlight
    return ZipAb


async def current_zip(page) -> str | None:
    """Текущий ZIP со страницы (``None`` если контрол ещё не найден).

    spotlight — из отдельного label; A/B — число из текста триггера
    ("Shipping to 19701")."""
    label = page.locator(ZipSpotlight.LABEL).first
    if await label.count():
        return (await label.inner_text()).strip()
    trigger = page.locator(ZipAb.TRIGGER).first
    if await trigger.count():
        m = re.search(r"(\d{3,})", await trigger.inner_text())
        return m.group(1) if m else None
    return None


async def _select_country(page, sel: type, country_option: str) -> None:
    """Выбирает страну по label. У A/B-варианта в диалоге несколько <select> —
    берём видимый со страновыми опциями ("Country - ISO3")."""
    selects = page.locator(sel.COUNTRY)
    n = await selects.count()
    for i in range(n):
        s = selects.nth(i)
        if await s.is_visible():
            opts = await s.locator("option").all_inner_texts()
            if opts and " - " in opts[0]:
                await s.select_option(label=country_option)
                return
    # spotlight: единственный select без проверки видимости (модал «не visible»)
    await selects.first.select_option(label=country_option)


async def set_zip(page, config: EbayConfig, timeout_ms: int = 12000) -> None:
    """Ставит ZIP из ``config``. Идемпотентно: если уже стоит — выходит сразу.
    Поддерживает оба варианта вёрстки. Бросает ZipChangeError, если после Apply
    ZIP не совпал."""
    if (await current_zip(page)) == config.zip:
        return

    sel = await _detect_variant(page, timeout_ms)

    await page.locator(sel.TRIGGER).first.click()
    await page.wait_for_selector(sel.COUNTRY, state="attached", timeout=timeout_ms)
    await _select_country(page, sel, config.country_option)
    await page.locator(sel.ZIP_INPUT).first.fill(config.zip)
    # Apply — видимый primary (в A/B-диалоге может быть несколько primary-кнопок).
    apply_btns = page.locator(sel.APPLY)
    for i in range(await apply_btns.count()):
        if await apply_btns.nth(i).is_visible():
            await apply_btns.nth(i).click()
            break

    # Apply перерисовывает SRP асинхронно — ждём, пока ZIP станет нужным.
    try:
        await _wait_zip(page, config.zip, timeout_ms)
    except Exception:
        raise ZipChangeError(
            f"ZIP not applied: want {config.zip}, got {await current_zip(page)}"
        )


async def _wait_zip(page, zip_code: str, timeout_ms: int) -> None:
    """Ждёт, пока текущий ZIP на странице не станет равным ``zip_code``."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_ms / 1000.0
    while True:
        if (await current_zip(page)) == zip_code:
            return
        if loop.time() >= deadline:
            raise TimeoutError("zip not applied")
        await page.wait_for_timeout(300)
