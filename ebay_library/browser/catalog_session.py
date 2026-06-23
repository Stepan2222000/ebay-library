"""Слой 2 — каталог-сессия (этапы 7–8): выдача через **in-page fetch** на живой
браузерной странице + контур восстановления (SPEC.md §5.1–5.3, §6.3, §7.2–7.3).

Одна прогретая страница на ``www.ebay.com`` = живая антибот-сессия. SRP запрашиваем
``fetch``-ом изнутри страницы (same-origin, ``credentials:'include'``); тело
классифицируем ``page_state.detect_state_html`` (по ``response.url`` + ``<title>``,
без DOM-ожидания), парсим ``parse_search_page``, переводим в USD (``convert_cards``).

ВОССТАНОВЛЕНИЕ (этап 8). Страница ОДНА на N потребителей → чиним под мьютексом, один
раз на всех. Живой прогон (2026-06-22) задал две вещи:
1. **Различаем по СОБЫТИЮ, не по тексту.** Обрыв in-flight fetch при нашей навигации
   даёт ``TypeError: Failed to fetch`` — дословно как реальный сетевой сбой, по тексту
   не отличить. Поэтому: ``_epoch`` (счётчик починок) — если во время моего fetch была
   починка → обрыв «наш» → переотправляем; если нет → настоящий сбой/смерть вкладки →
   критично (§7.2). Плюс ``_ready`` (ворота): пока идёт починка, новые fetch не стартуют
   (иначе fetch, начатый прямо в момент навигации, тоже оборвётся и ложно сочтётся сбоем).
2. **Pardon чиним повторным ``warmup`` (на главную), а НЕ навигацией на упавший URL:**
   goto на упавший SRP-URL ~17% ломал следующий fetch, на главную — 6/6 ok.

- **Pardon** → ``_recover_pardon`` (повторный ``warmup`` на главную) + ``controller.backoff_now``;
- **Access Denied** → ``_swap`` (новая страница из ``get_page`` — новый прокси) +
  ``controller.reset``; старую страницу НЕ закрываем (утилизирует оркестратор);
- **Error Page / неожиданный тип / смерть вкладки / непойманное** → критично (наружу).
Без лимита починок (контроллер тормозит).

⚠️  ПРЕДВАРИТЕЛЬНО (под живую доводку): контур проверен ОФЛАЙН (мок-страница) + механика
обрыва живьём, но end-to-end под РЕАЛЬНЫМ Pardon не прогонялся — антибот на прогретой
сессии не вызывается (страховка). Живьём проверяется только инжектом синтетики.
"""

from __future__ import annotations

import asyncio
import logging

from ..errors import ErrorPageError
from ..html.page_state import Antibot, PageKind, detect_state_html
from ..html.srp import parse_search_page
from ..http.fx import convert_cards
from ..models import Catalog, CatalogItem, CatalogResult, SrpCard
from ..urls import ITEMS_PER_PAGE, build_search_url

logger = logging.getLogger("ebay_library")

_HOME_URL = "https://www.ebay.com/"
MAX_PAGES = 5

# JS: same-origin fetch с куками. Возвращаем финальный URL (после редиректов — Pardon
# уводит на /splashui/challenge), статус и полное тело. p50-латентность — performance.now().
_FETCH_JS = """
async (url) => {
  const t0 = performance.now();
  const r = await fetch(url, { credentials: 'include' });
  const body = await r.text();
  return { url: r.url, status: r.status, body, ms: performance.now() - t0 };
}
"""


async def warmup(page) -> None:
    """Прогрев = заход на главную eBay → страница получает/обновляет антибот-клиренс.

    Один примитив для трёх ситуаций: первичный прогрев (старт), прогрев новой страницы
    (swap при Access Denied) и снятие Pardon (повторный прогрев, ``_recover_pardon``).
    Без него первый SRP ловит антифрод (SPEC.md §5.1)."""
    await page.goto(_HOME_URL, wait_until="domcontentloaded")
    logger.debug("catalog warmup ok: %s", page.url)


async def in_page_fetch(page, url: str) -> dict:
    """``fetch`` URL изнутри страницы (same-origin, с куками). → {url, status, body, ms}."""
    return await page.evaluate(_FETCH_JS, url)


class CatalogSession:
    """Каталог-сессия: владеет текущей страницей + контуром восстановления (см. шапку).

    ``get_page`` — async-колбэк оркестратора → свежая Playwright-страница (новый
    контекст/прокси); зовётся при старте и на каждый swap. ``controller`` — адаптивный
    ``Controller`` (опц.): ``backoff_now()`` на Pardon, ``reset()`` на Access Denied.
    ``_lock`` сериализует только починку; обычные ``fetch_srp`` от N потребителей идут
    параллельно."""

    def __init__(self, get_page, *, controller=None, on_fetch=None):
        self._get_page = get_page
        self._controller = controller
        self._on_fetch = on_fetch          # колбэк(ms) после успешного SRP-fetch — канал
        self._page = None                  # per-fetch метрик воркеру (адаптив, SPEC.md §8)
        self._lock = asyncio.Lock()        # мьютекс починки (один recover/swap на всех)
        self._epoch = 0                    # счётчик починок (различает «оборвала починка» vs сбой)
        self._ready = asyncio.Event()      # ворота: установлено, когда НЕ идёт починка
        self._ready.set()

    @property
    def page(self):
        return self._page

    @property
    def epoch(self) -> int:
        return self._epoch

    async def start(self):
        """Берёт первую страницу у оркестратора и прогревает её."""
        self._page = await self._get_page()
        await warmup(self._page)
        return self._page

    async def fetch_srp(self, url: str) -> str:
        """In-page fetch SRP + восстановление (см. шапку). Возвращает HTML готовой
        выдачи; Error Page / неожиданный тип / смерть вкладки → критично наружу."""
        while True:
            await self._ready.wait()        # идёт починка — новый fetch ждёт (не стартуем в навигацию)
            epoch = self._epoch
            page = self._page
            try:
                res = await in_page_fetch(page, url)
            except Exception:
                if self._epoch != epoch:    # починка во время моего fetch → обрыв «наш» → повтор
                    continue
                raise                        # настоящий сбой / смерть вкладки → критично (§7.2)
            state = detect_state_html(res["url"], res["body"])
            if state.antibot is Antibot.ERROR_PAGE:
                raise ErrorPageError(f"Error Page at {res['url']}")
            if state.antibot is Antibot.ACCESS_DENIED:
                await self._swap(epoch)
                continue
            if state.antibot is Antibot.PARDON or state.kind is PageKind.PARDON:
                await self._recover_pardon(epoch)
                continue
            if state.kind is not PageKind.SRP:
                raise ErrorPageError(f"unexpected page kind {state.kind.value} at {res['url']}")
            if self._on_fetch is not None:  # per-fetch латентность (мс) → окно контроллера
                self._on_fetch(res["ms"])
            return res["body"]

    async def _recover_pardon(self, epoch: int) -> None:
        """Pardon: повторный ``warmup`` (на главную) снимает антибот и оставляет рабочую
        страницу; goto на упавший URL ломал следующий fetch (живой тест). Один на всех."""
        async with self._lock:
            if self._epoch != epoch:        # уже починил другой потребитель
                return
            logger.info("catalog Pardon → re-warmup (главная)")
            self._ready.clear()             # ворота: новые fetch ждут
            self._epoch += 1                # ДО навигации: летящие aborts увидят смену → повтор
            try:
                await warmup(self._page)
                if self._controller is not None:
                    self._controller.backoff_now()
            finally:
                self._ready.set()

    async def _swap(self, epoch: int) -> None:
        """Access Denied: новая страница от оркестратора (новый прокси). Один на всех."""
        async with self._lock:
            if self._epoch != epoch:
                return
            logger.info("catalog Access Denied → swap page (get_page)")
            self._ready.clear()
            try:
                page = await self._get_page()   # новый прокси/контекст — даёт оркестратор
                await warmup(page)
                self._page = page               # старую НЕ закрываем (утилизирует оркестратор)
                self._epoch += 1
                if self._controller is not None:
                    self._controller.reset()
            finally:
                self._ready.set()

    async def fetch_catalog(
        self,
        queries: str | list[str],
        *,
        zip: str | None = None,
        condition: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        max_pages: int = MAX_PAGES,
    ) -> CatalogResult:
        """Каталоги по запросу или СПИСКУ запросов (группа артикулов) через in-page fetch
        с восстановлением. Объединение каталогов группы: глобальный дедуп по item_id в
        ``items`` + ``per_query`` по каждому артикулу.

        По каждому запросу листаем страницы выдачи (стоп: сепаратор fewer-words, неполная
        страница либо ``max_pages``; нахлёст гасится дедупом), затем один fx-батч в USD
        (SPEC.md §5.4). Дубликаты запросов отбрасываются. ``zip`` нужен всегда (рекоменд.
        "19701"). Прогресс артикула при починке сохраняется: уже распарсенные страницы не
        теряем — ``fetch_srp`` чинит и повторяет ВНУТРИ себя."""
        qlist = [queries] if isinstance(queries, str) else list(dict.fromkeys(queries))
        filters = dict(zip=zip, condition=condition, min_price=min_price, max_price=max_price)

        items: list[CatalogItem] = []
        seen: set[str] = set()
        per_query: dict[str, Catalog] = {}

        for query in qlist:
            cards: list[SrpCard] = []
            qseen: set[str] = set()
            results_count = 0
            pages_fetched = 0
            has_sep = False
            pgn = 1
            while True:
                url = build_search_url(query, page=pgn, **filters)
                html = await self.fetch_srp(url)
                sp = parse_search_page(html)
                pages_fetched += 1
                logger.debug("srp %r pgn=%d: %d cards", query, pgn, len(sp.items))
                if pgn == 1:
                    results_count = sp.results_count
                has_sep = has_sep or sp.has_fewer_words_sep
                for c in sp.items:
                    if c.item_id not in qseen:
                        qseen.add(c.item_id)
                        cards.append(c)
                if sp.has_fewer_words_sep or len(sp.items) < ITEMS_PER_PAGE:
                    break
                if pgn >= max_pages:
                    break
                pgn += 1

            converted = await convert_cards(cards)
            per_query[query] = Catalog(
                query=query, results_count=results_count, items=converted,
                pages_fetched=pages_fetched, has_fewer_words_sep=has_sep,
            )
            for it in converted:
                if it.item_id not in seen:
                    seen.add(it.item_id)
                    items.append(it)

        return CatalogResult(items=items, per_query=per_query)
