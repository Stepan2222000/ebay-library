"""Item-воркер (этап 6): ``run_item_worker`` — пул потребителей тянет товары
параллельно с адаптивной конкуренцией и пишет в БД. Связывает этапы 1–5.

Контракт с оркестратором (SPEC.md §6):
- ``next_task()`` — async, **потокобезопасна** (зовётся параллельно потребителями),
  возвращает **одну** задачу ``{"item_id": "..."}`` или ``None`` (= задач сейчас нет
  → ждём, воркер НЕ завершается). Воркер перманентный: останавливается только по
  критической ошибке (исключение наружу) или отмене снаружи.
- ``task_done(task, stats)`` — async, **строго после записи**. ``stats`` =
  ``{"db": <результат apply_item>, "timing": {"stages": {...}, "total_ms": ...}}``
  (стадии: wait/fetch/desc/parse/write).
- ``store`` — клиент записи (его жизненный цикл — у оркестратора; воркер не закрывает).

«По жёсткому» (SPEC.md §7.2): любая критическая ошибка потребителя (``ParseError`` /
``TransportError`` / ошибка записи) → отменяем всех + пробрасываем исключение; задачи
«в полёте» без ``task_done`` оркестратор переотдаёт (модель «каждый пишет сам» —
очереди/flush нет).

⚠️  ВНИМАНИЕ: логика МАСШТАБИРОВАНИЯ конкуренции (лимитер + ленивый пул + петля
контроллера + параметры окна) — **ПРЕДВАРИТЕЛЬНАЯ**, проверена на синтетике/мок-нагрузке,
но НЕ на живом трафике; возможно неверна и требует доработки (как и сам ``Controller``,
см. concurrency.py / SPEC.md §8.4). Внутренний потолок ``ceiling`` (= ``max_clients``
сессии) — предохранитель от разноса, не «настоящий» лимит (колено латентности ниже).
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from contextlib import suppress

from .concurrency import CatalogController, Controller
from .http.fetch import make_item_session
from .item import fetch_item_page
from .store import Store

logger = logging.getLogger("ebay_library")


class StageTimer:
    """Поэтапный замер времени задачи (стадии wait/fetch/desc/parse/write).

    ``switch(name)`` закрывает интервал предыдущей стадии и открывает новую — каждый
    момент отнесён ровно к одной стадии. ``stop()`` финализирует. Времена — дробные мс."""

    def __init__(self, loop):
        self._loop = loop
        self.stages: dict[str, float] = {}
        self._cur: str | None = None
        self._t: float = 0.0

    def switch(self, name: str | None) -> None:
        now = self._loop.time()
        if self._cur is not None:
            self.stages[self._cur] = self.stages.get(self._cur, 0.0) + (now - self._t)
        self._cur = name
        self._t = now

    def stop(self) -> None:
        self.switch(None)

    def ms(self) -> dict[str, float]:
        return {k: round(v * 1000, 2) for k, v in self.stages.items()}


class _Limiter:
    """Ресайзимый лимитер: держит ``target`` (= C), допускает ``inflight < target``
    одновременных держателей. ``set_target`` меняет лимит на лету (рост — будим ждущих;
    срез — новые ``acquire`` ждут, «в полёте» не трогаем). Заменяет ``asyncio.Semaphore``
    (тот не ресайзится)."""

    def __init__(self, target: int):
        self._target = target
        self.inflight = 0
        self._cond = asyncio.Condition()

    async def acquire(self) -> None:
        async with self._cond:
            while self.inflight >= self._target:
                await self._cond.wait()
            self.inflight += 1

    async def release(self) -> None:
        async with self._cond:
            self.inflight -= 1
            self._cond.notify_all()

    async def set_target(self, target: int) -> None:
        async with self._cond:
            self._target = target
            self._cond.notify_all()


async def run_item_worker(
    next_task,
    task_done,
    store: Store,
    *,
    zip: str = "19701",
    start_c: int = 4,
    ceiling: int = 512,
    window: int = 64,
    max_window_s: float = 2.0,
    idle_s: float = 0.2,
) -> None:
    """Перманентный item-воркер: пул потребителей + адаптивная конкуренция.

    Параметры масштабирования (``start_c``/``ceiling``/``window``/…) —
    **предварительные** (см. шапку), под живой тюнинг. Возвращается НЕ сам по себе:
    только наружу-исключением (критическая ошибка) или при отмене корутины."""
    loop = asyncio.get_event_loop()
    limiter = _Limiter(start_c)
    ctrl = Controller()
    death = asyncio.Event()
    failure: dict = {}
    win_lats: list[float] = []
    win_start = loop.time()
    consumers: list[asyncio.Task] = []

    def _fail(exc: BaseException, task) -> None:
        if "exc" not in failure:
            failure["exc"] = exc
            failure["task"] = task
            with suppress(Exception):
                exc.task = task
            logger.error("item worker dying at task %.200s (%s: %.200s)",
                         task, type(exc).__name__, exc)
        death.set()

    async def consumer(session) -> None:
        while not death.is_set():
            prof = StageTimer(loop)
            prof.switch("wait")
            await limiter.acquire()
            try:
                task = await next_task()
                if task is None:                       # задач сейчас нет → ждём (SPEC §6)
                    await limiter.release()
                    await asyncio.sleep(idle_s)
                    continue
                t0 = loop.time()
                item = await fetch_item_page(session, task["item_id"], prof=prof)
                latency = loop.time() - t0             # сигнал для контроллера (B: fetch_item_page)
                prof.switch("write")
                db = await store.apply_item(item, zip=zip)
                prof.stop()
                win_lats.append(latency)
                await task_done(task, {
                    "db": db,
                    "timing": {"stages": prof.ms(),
                               "total_ms": round(sum(prof.stages.values()) * 1000, 2)},
                })
                await limiter.release()
            except asyncio.CancelledError:
                raise
            except Exception as e:                     # критично (по жёсткому, §7.2)
                with suppress(Exception):
                    await limiter.release()
                _fail(e, locals().get("task"))
                return

    def ensure_pool(c: int, session) -> None:
        """Ленивый рост пула: спавним потребителей до min(C, ceiling). Срез C — лишние
        паркуются на лимитере (не убиваем)."""
        while len(consumers) < min(c, ceiling):
            consumers.append(asyncio.create_task(consumer(session)))

    async def controller_loop() -> None:
        """Раз в окно (W завершений или max_window_s) меряем throughput + latency-p50
        → Controller.update → ресайз лимитера + рост пула. degraded=False (item:
        латентность-онли, SPEC §8 / этап 6 решение A)."""
        nonlocal win_start
        while not death.is_set():
            await asyncio.sleep(0.05)
            n = len(win_lats)
            elapsed = loop.time() - win_start
            if n >= window or (n > 0 and elapsed >= max_window_s):
                lats = win_lats[:]
                win_lats.clear()
                tput = n / elapsed if elapsed > 0 else 0.0
                p50 = statistics.median(lats)
                c = ctrl.update(tput, p50, degraded=False)
                await limiter.set_target(c)
                ensure_pool(c, _session)
                logger.debug("adaptive: C=%d tput=%.0f/s p50=%dms pool=%d",
                             c, tput, round(p50 * 1000), len(consumers))
                win_start = loop.time()

    _session = None
    async with make_item_session(max_clients=ceiling) as session:
        _session = session
        ensure_pool(start_c, session)
        cloop = asyncio.create_task(controller_loop())
        try:
            await death.wait()                         # перманентно: до смерти/отмены
        finally:
            for t in consumers:
                t.cancel()
            cloop.cancel()
            await asyncio.gather(*consumers, cloop, return_exceptions=True)
    if "exc" in failure:                               # критическая смерть → наружу
        raise failure["exc"]


async def run_catalog_worker(
    get_page,
    next_task,
    task_done,
    store: Store,
    *,
    zip: str = "19701",
    start_c: int = 8,
    window: int = 48,
    max_window_s: float = 2.0,
    idle_s: float = 0.2,
) -> None:
    """Перманентный каталог-воркер (этап 9, SPEC.md §6.3): пул потребителей над ОДНОЙ
    ``CatalogSession`` (одна прогретая вкладка на ``get_page``-прокси) + адаптивная
    конкуренция ``CatalogController``.

    Задача (``next_task``) = БАТЧ: ``{"articles": [...], "condition"?, "min_price"?,
    "max_price"?}`` (один артикул = список из одного). Потребитель: один вызов
    ``fetch_catalog(articles, …)`` (последовательно листает страницы, fx→USD) → по каждому
    артикулу из ``per_query`` пишет ``store.apply_catalog`` → ``task_done`` СТРОГО после
    записи всех артикулов батча.

    Конкуренция (SPEC.md §8): сигнал — **per-fetch ``ms``** (через ``on_fetch`` сессии),
    не время задачи (батч многостраничен — время задачи ≠ насыщение). Антибот сессия
    гасит сама на общем ``ctrl`` (``backoff_now``/``reset``); петля окна — ``degraded=
    False``, латентность только стопит рост (см. CatalogController). Окно чистим при
    починке (смена ``epoch``) — оно грязное. Лимитер каждый тик ре-синкаем к ``ctrl.C``,
    чтобы backoff из сессии доезжал быстро.

    «По жёсткому» (SPEC.md §7.2): любая критика (ParseError / ErrorPage / транспортная
    смерть вкладки / сбой fx или записи) из ``fetch_catalog``/``apply_catalog`` → весь пул
    умирает, исключение наружу; батчи «в полёте» без ``task_done`` оркестратор переотдаёт.
    Access Denied — НЕ критика: сессия меняет страницу сама (§7.3). Восстановление воркера
    после критики — забота оркестратора (новый ``get_page``-прокси, §6.4).

    ⚠️  ПРЕДВАРИТЕЛЬНО (как и item-воркер): масштабирование/окно/пороги — под живой тюнинг
    (SPEC.md §8.4). Масштаб до 950+/мин — несколькими воркерами по числу прокси (под
    вопросом, этап 9)."""
    from .browser.catalog_session import CatalogSession      # ленивый импорт (item-лейну не нужен)

    loop = asyncio.get_event_loop()
    limiter = _Limiter(start_c)
    ctrl = CatalogController(start=start_c)
    death = asyncio.Event()
    failure: dict = {}
    win_ms: list[float] = []                # per-fetch ms текущего окна (через on_fetch)
    win_start = loop.time()
    applied = start_c
    last_epoch = 0
    consumers: list[asyncio.Task] = []

    def _fail(exc: BaseException, task) -> None:
        if "exc" not in failure:
            failure["exc"] = exc
            failure["task"] = task
            with suppress(Exception):
                exc.task = task
            logger.error("catalog worker dying at task %.200s (%s: %.200s)",
                         task, type(exc).__name__, exc)
        death.set()

    session = CatalogSession(get_page, controller=ctrl, on_fetch=win_ms.append)
    await session.start()

    async def consumer() -> None:
        while not death.is_set():
            await limiter.acquire()
            try:
                task = await next_task()
                if task is None:                       # задач нет → ждём (SPEC §6)
                    await limiter.release()
                    await asyncio.sleep(idle_s)
                    continue
                filters = dict(condition=task.get("condition"),
                               min_price=task.get("min_price"), max_price=task.get("max_price"))
                result = await session.fetch_catalog(task["articles"], zip=zip, **filters)
                t0 = loop.time()
                per_article = []
                for article, catalog in result.per_query.items():
                    db = await store.apply_catalog(article, catalog, zip=zip, **filters)
                    per_article.append({"article": article, "db": db, "n_cards": len(catalog.items)})
                await task_done(task, {"per_article": per_article,
                                       "write_ms": round((loop.time() - t0) * 1000, 2)})
                await limiter.release()
            except asyncio.CancelledError:
                raise
            except Exception as e:                     # критично (по жёсткому, §7.2)
                with suppress(Exception):
                    await limiter.release()
                _fail(e, locals().get("task"))
                return

    def ensure_pool(c: int) -> None:
        while len(consumers) < c:                      # без потолка (контроллер тормозит)
            consumers.append(asyncio.create_task(consumer()))

    async def controller_loop() -> None:
        nonlocal win_start, applied, last_epoch
        while not death.is_set():
            await asyncio.sleep(0.05)
            if session.epoch != last_epoch:            # была починка → окно грязное, выкинуть
                last_epoch = session.epoch
                win_ms.clear()
                win_start = loop.time()
                if ctrl.C != applied:
                    await limiter.set_target(ctrl.C); applied = ctrl.C
                continue
            n = len(win_ms)
            elapsed = loop.time() - win_start
            if n >= window or (n >= 8 and elapsed >= max_window_s):
                lats = win_ms[:]
                win_ms.clear()
                tput = n / elapsed if elapsed > 0 else 0.0
                p50 = statistics.median(lats)
                c = ctrl.update(tput, p50, degraded=False)
                await limiter.set_target(c); applied = c
                ensure_pool(c)
                logger.debug("catalog adaptive: C=%d tput=%.0f/s p50=%dms pool=%d",
                             c, tput, round(p50), len(consumers))
                win_start = loop.time()
            elif ctrl.C != applied:                    # ре-синк backoff_now/reset из сессии
                await limiter.set_target(ctrl.C); applied = ctrl.C

    ensure_pool(start_c)
    cloop = asyncio.create_task(controller_loop())
    try:
        await death.wait()                             # перманентно: до смерти/отмены
    finally:
        for t in consumers:
            t.cancel()
        cloop.cancel()
        await asyncio.gather(*consumers, cloop, return_exceptions=True)
    if "exc" in failure:                               # критическая смерть → наружу
        raise failure["exc"]
