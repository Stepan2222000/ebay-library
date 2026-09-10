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

Единый контур ``_recover(need_new_page)`` для всех транзиентов (доказано стендом на
сервере 31.77.159.82, 8 потребителей, все аварии → 0 потерь):
- **Pardon / nav (страницу увели) / blocked (fetch зарезан)** → прогрев текущей страницы
  (``_recover(need_new_page=False)``) + ``controller.backoff_now``;
- **Access Denied / dead (смерть вкладки)** → новая страница из ``get_page`` — новый прокси
  (``_recover(need_new_page=True)``) + ``controller.reset``; старую НЕ закрываем;
- прогрев не идёт → ``WARMUP_TRIES`` попыток → эскалация в новую страницу;
- новую тоже не взять/прогреть (обычно мёртвый браузер) → сессия помечается мёртвой,
  ``SessionDeadError`` наружу: оркестратор пересоздаёт браузер+сессию (не ретрай задачи).
Транзиент теряет не деталь, а лишь пару секунд: fetch чинится и повторяется ВНУТРИ.
Наружу критично только Error Page / неожиданный тип / незнакомый сбой fetch (§7.2).
Без лимита починок на fetch (контроллер тормозит; внешний PART_TIMEOUT — потолок).
"""

from __future__ import annotations

import asyncio
import logging

from ..errors import ErrorPageError, SessionDeadError
from ..html.page_state import Antibot, PageKind, detect_state_html, srp_rendered
from ..html.srp import parse_search_page
from ..http.fx import convert_cards
from ..models import Catalog, CatalogItem, CatalogResult, SrpCard
from ..urls import ITEMS_PER_PAGE, build_search_url

logger = logging.getLogger("ebay_library")

_HOME_URL = "https://www.ebay.com/"
MAX_PAGES = 5

# JS: same-origin fetch с куками. Возвращаем финальный URL (после редиректов — Pardon
# уводит на /splashui/challenge), статус и полное тело. p50-латентность — performance.now().
# AbortSignal.timeout: висящий fetch (мёртвый прокси/blackhole) сам оборвётся ошибкой,
# а не повиснет навечно — иначе деталь ждёт весь внешний PART_TIMEOUT (живой инцидент 02.07).
_FETCH_JS = """
async ({url, timeout_ms}) => {
  const t0 = performance.now();
  const r = await fetch(url, { credentials: 'include', signal: AbortSignal.timeout(timeout_ms) });
  const body = await r.text();
  return { url: r.url, status: r.status, body, ms: performance.now() - t0 };
}
"""


def _fetch_error_kind(exc: Exception) -> str | None:
    """Классифицирует сбой in-page fetch по СОБЫТИЮ (текст исключения playwright).
    → 'nav'  : страницу увели навигацией (challenge/reload eBay) — лечится прогревом;
      'dead' : вкладка/таргет умерли — нужна новая страница (прогрев трупа бесполезен);
      'blocked': fetch зарезан (CSP challenge-страницы / сеть / таймаут) — лечится прогревом;
      None   : незнакомый сбой — наружу без ретраев (честный фатал, SPEC.md §7.2).
    Разделение доказано стендом (сервер 31.77.159.82): nav/blocked снимаются goto на
    главную, dead — только swap."""
    s = str(exc)
    if "Execution context was destroyed" in s:
        return "nav"
    if "Target crashed" in s or "Target closed" in s or "has been closed" in s:
        return "dead"
    if "Failed to fetch" in s or "signal timed out" in s or "TimeoutError" in s:
        return "blocked"
    return None


async def warmup(page) -> None:
    """Прогрев = заход на главную eBay → страница получает/обновляет антибот-клиренс.

    Один примитив для трёх ситуаций: первичный прогрев (старт), прогрев новой страницы
    (swap при Access Denied/смерти вкладки) и снятие Pardon/челленджа (повторный прогрев,
    ``_heal``). Без него первый SRP ловит антифрод (SPEC.md §5.1)."""
    await page.goto(_HOME_URL, wait_until="domcontentloaded")
    logger.debug("catalog warmup ok: %s", page.url)


async def in_page_fetch(page, url: str, timeout_ms: int, eval_timeout_s: float) -> dict:
    """``fetch`` URL изнутри страницы (same-origin, с куками). → {url, status, body, ms}.

    Двойной таймаут: ``AbortSignal`` рвёт сам fetch внутри страницы, а внешний
    ``wait_for`` страхует от НЕМОГО зависания — крэшнутая вкладка не бросает ошибку из
    ``evaluate``, а молчит навсегда (стенд 31.77.159.82). Внешний таймаут = таймаут
    fetch + запас; сработал → вкладку считаем мёртвой (``asyncio.TimeoutError`` наверх)."""
    return await asyncio.wait_for(
        page.evaluate(_FETCH_JS, {"url": url, "timeout_ms": timeout_ms}),
        timeout=eval_timeout_s)


class CatalogSession:
    """Каталог-сессия: владеет текущей страницей + контуром восстановления (см. шапку).

    ``get_page`` — async-колбэк оркестратора → свежая Playwright-страница (новый
    контекст/прокси); зовётся при старте и на каждый swap. ``controller`` — адаптивный
    ``Controller`` (опц.): ``backoff_now()`` на Pardon, ``reset()`` на Access Denied.
    ``_lock`` сериализует только починку; обычные ``fetch_srp`` от N потребителей идут
    параллельно."""

    FETCH_TIMEOUT_MS = 60_000     # таймаут самого fetch (AbortSignal внутри страницы)
    EVAL_TIMEOUT_S = 75.0         # внешний потолок evaluate = fetch + запас (немой крэш вкладки)
    WARMUP_TRIES = 10            # попыток прогрева до эскалации в новую страницу (§7.3)
    WARMUP_PAUSE_S = 1.0        # пауза между попытками прогрева
    SHELL_RETRIES = 3           # SRP-заглушка eBay (200 без результатов) → повторы fetch
    SHELL_PAUSE_S = 2.0         # пауза между ними
    RECOVER_TIMEOUT_S = 120.0    # потолок на весь контур починки: get_page на мёртвом
                                 # браузере виснет навсегда → без него тихий дедлок (инцидент 02.07)

    def __init__(self, get_page, *, controller=None, on_fetch=None):
        self._get_page = get_page
        self._controller = controller
        self._on_fetch = on_fetch          # колбэк(ms) после успешного SRP-fetch — канал
        self._page = None                  # per-fetch метрик воркеру (адаптив, SPEC.md §8)
        self._lock = asyncio.Lock()        # мьютекс починки (один recover/swap на всех)
        self._epoch = 0                    # счётчик починок (различает «оборвала починка» vs сбой)
        self._ready = asyncio.Event()      # ворота: установлено, когда НЕ идёт починка
        self._ready.set()
        self._dead = False                 # сессия признана мёртвой → все fetch падают сразу

    @property
    def page(self):
        return self._page

    @property
    def epoch(self) -> int:
        return self._epoch

    async def start(self):
        """Берёт первую страницу у оркестратора и прогревает её (с ретраями)."""
        self._page = await self._get_page()
        await self._warmup_retries(self._page)
        return self._page

    async def fetch_srp(self, url: str) -> str:
        """In-page fetch SRP + восстановление (см. шапку). Возвращает HTML готовой выдачи.

        Транзиент (антибот увёл страницу / зарезал fetch / смерть вкладки) — это событие
        СЕССИИ, а не сбой задачи: чиним и повторяем внутри себя, наружу деталь не теряется.
        Наружу летят только: Error Page / неожиданный тип (§7.2), незнакомый сбой fetch и
        ``SessionDeadError`` (сессию не починить — оркестратор пересоздаёт браузер)."""
        shells = 0                          # подряд полученных SRP-заглушек (см. srp_rendered)
        while True:
            await self._ready.wait()        # идёт починка — новый fetch ждёт (не стартуем в навигацию)
            if self._dead:                  # сессия мертва → сразу наружу (не виснем на трупе)
                raise SessionDeadError("catalog session dead")
            epoch = self._epoch
            page = self._page
            try:
                res = await in_page_fetch(page, url, self.FETCH_TIMEOUT_MS, self.EVAL_TIMEOUT_S)
            except asyncio.TimeoutError:
                if self._epoch != epoch:    # починка во время моего fetch → обрыв «наш» → повтор
                    continue
                kind = "dead"               # evaluate молчит дольше таймаута → вкладка невменяема
            except Exception as e:
                if self._epoch != epoch:
                    continue
                kind = _fetch_error_kind(e)
                if kind is None:            # незнакомый сбой → критично наружу (§7.2)
                    raise
            else:
                state = detect_state_html(res["url"], res["body"])
                if state.antibot is Antibot.ERROR_PAGE:
                    raise ErrorPageError(f"Error Page at {res['url']}")
                if state.antibot is Antibot.ACCESS_DENIED:
                    await self._recover(epoch, need_new_page=True)   # новый прокси
                    continue
                if state.antibot is Antibot.PARDON or state.kind is PageKind.PARDON:
                    await self._recover(epoch, need_new_page=False)  # снять челлендж прогревом
                    continue
                if state.kind is not PageKind.SRP:
                    raise ErrorPageError(f"unexpected page kind {state.kind.value} at {res['url']}")
                if not srp_rendered(res["body"]):
                    # серверная заглушка eBay вместо выдачи — транзиент: повторяем fetch;
                    # исчерпание → ErrorPageError (лейн даёт детали второй заход), не ParseError.
                    shells += 1
                    if shells > self.SHELL_RETRIES:
                        raise ErrorPageError(f"SRP not rendered after {shells - 1} retries at {res['url']}")
                    logger.warning("SRP-заглушка eBay (%d/%d) → повтор: %s", shells, self.SHELL_RETRIES, url)
                    await asyncio.sleep(self.SHELL_PAUSE_S)
                    continue
                if self._on_fetch is not None:  # per-fetch латентность (мс) → окно контроллера
                    self._on_fetch(res["ms"])
                return res["body"]
            # сюда попадаем только на восстановимом сбое fetch (nav/blocked/dead)
            await self._recover(epoch, need_new_page=(kind == "dead"))

    async def _recover(self, epoch: int, *, need_new_page: bool) -> None:
        """Единый контур починки — один лекарь на всех (мьютекс), остальные ждут у ворот
        и повторяют fetch. Всё под таймаутом: get_page на мёртвом браузере виснет навсегда;
        не вылечили → сессия помечается мёртвой (``SessionDeadError`` наружу)."""
        async with self._lock:
            if self._dead:
                raise SessionDeadError("catalog session dead")
            if self._epoch != epoch:        # уже починил другой потребитель
                return
            self._ready.clear()             # ворота: новые fetch ждут
            self._epoch += 1                # ДО навигации: летящие aborts увидят смену → повтор
            try:
                await asyncio.wait_for(self._heal(need_new_page),
                                       timeout=self.RECOVER_TIMEOUT_S)
            except Exception as e:
                self._dead = True           # все ждущие упадут сразу, никто не виснет на трупе
                logger.error("catalog session dead: recovery failed (%.90s)", e)
                raise SessionDeadError(str(e)) from e
            finally:
                self._ready.set()

    async def _heal(self, need_new_page: bool) -> None:
        """Прогрев текущей страницы; при провале (или сразу для мёртвой вкладки) —
        новая страница от оркестратора. Не прогрелась и она → наружу (→ сессия мертва)."""
        if not need_new_page:
            try:
                await self._warmup_retries(self._page)
                if self._controller is not None:
                    self._controller.backoff_now()
                return
            except Exception as e:
                logger.error("catalog re-warmup не удался за %d попыток (%.90s) → новая страница",
                             self.WARMUP_TRIES, e)
        page = await self._get_page()       # новый прокси/контекст — даёт оркестратор
        await self._warmup_retries(page)    # старую НЕ закрываем (утилизирует оркестратор)
        self._page = page
        if self._controller is not None:
            self._controller.reset()

    async def _warmup_retries(self, page) -> None:
        """``warmup`` до ``WARMUP_TRIES`` попыток; все упали → исключение наружу. Транзиентный
        обрыв прогрева (ERR_ABORTED на редиректе антибота, живой тест) — не смертельный."""
        last = None
        for i in range(1, self.WARMUP_TRIES + 1):
            try:
                await warmup(page)
                if i > 1:
                    logger.info("catalog warmup удался с попытки %d", i)
                return
            except Exception as e:
                last = e
                logger.warning("catalog warmup %d/%d не удался: %.90s", i, self.WARMUP_TRIES, e)
                await asyncio.sleep(self.WARMUP_PAUSE_S)
        raise last

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
