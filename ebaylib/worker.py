"""Слой воркера — ``run_worker``: бесконечный цикл «задача → парсинг → запись».

Воркер отдаёт три вещи: ``get_page`` (свежая страница; браузер/прокси — его
зона), ``next_task`` (источник задач: вернул dict-задачу; ``None`` — штатное
завершение; если задач пока нет — просто ждёт внутри) и ``store`` (клиент
записи в ebay_data). Опционально ``task_done(task, stats)`` — подтверждение,
вызывается строго ПОСЛЕ записи результата в БД: задача обработана = записана.
``stats = {"db": …, "timing": {"started_at", "parse_ms", "write_ms",
"total_ms"}}`` — статистика записи из БД + тайминги этапов (замеряет
библиотека).

Формат задачи (всё вне ``params`` — метаданные оркестратора, библиотека их
не читает и возвращает в ``task_done`` как есть):

    {"type": "catalog", "params": {"articles": ["805079T", "805079"],  # или строка
                                   "zip": "19701",
                                   "condition": "new" | "used" | "all",   # опц.
                                   "min_price": 50, "max_price": 500}}    # опц.
    {"type": "item",    "params": {"item_id": "277574984378", "zip": "19701"}}

Неизвестный ``type`` или кривые ``params`` — критическая ошибка (баг
оркестратора, TaskFormatError).

Парсинг и запись перекрываются: готовые результаты уходят в ограниченную
очередь, фоновый писатель пишет их в БД, парсинг тем временем берёт следующую
задачу. Любая ошибка валит воркер целиком (политика «без фолбеков»); при
ошибке ПАРСИНГА уже готовые результаты из очереди дописываются в БД перед
смертью (добытое не выбрасываем), при ошибке ЗАПИСИ — умираем сразу, даже
если парсинг висит в ожидании задач. Незаписанные задачи переотдаёт
оркестратор (по ним не было ``task_done``).

Задача-виновница при смерти: пишется в лог (ERROR) и прикладывается к
исключению атрибутом ``task`` (для смерти записи — задача, которая писалась;
для смерти парсинга — парсящаяся; сбой ``next_task`` — без задачи).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone

from .browser.session import PAGE_DELAY_S, EbaySession
from .store import Store

logger = logging.getLogger("ebaylib")

QUEUE_SIZE = 100   # максимум готовых результатов между парсингом и записью
_STOP = object()   # сентинел: писатель, дописав очередь, завершается


class TaskFormatError(Exception):
    """Задача не соответствует контракту (type/params) — баг оркестратора,
    критично: воркер умирает."""


def _blame(e: BaseException, task: dict, where: str) -> None:
    """Называет задачу-виновницу: ERROR в лог + атрибут ``task`` на исключении
    (оркестратор может прочитать программно). Best-effort: исключения со
    __slots__ атрибут не примут — лог всё равно останется."""
    logger.error("%s at task %.300s (%s: %.200s)", where, task, type(e).__name__, e)
    try:
        e.task = task
    except Exception:
        pass


def _params(task) -> dict:
    if not isinstance(task, dict) or not isinstance(task.get("params"), dict):
        raise TaskFormatError(f"task must be a dict with 'params' dict, got: {task!r}")
    return task["params"]


def _require(params: dict, key: str):
    value = params.get(key)
    if value is None:
        raise TaskFormatError(f"task params missing required {key!r}")
    return value


async def _dispatch(session: EbaySession, task: dict):
    """Тип задачи → вызов сессии. Возвращает CatalogResult | ItemPage."""
    params = _params(task)
    kind = task.get("type")
    if kind == "catalog":
        return await session.fetch_catalog(
            _require(params, "articles"),
            zip=_require(params, "zip"),
            condition=params.get("condition"),
            min_price=params.get("min_price"),
            max_price=params.get("max_price"),
        )
    if kind == "item":
        return await session.fetch_item(
            _require(params, "item_id"), zip=_require(params, "zip")
        )
    raise TaskFormatError(f"unknown task type {kind!r}")


async def _write_result(store: Store, task_done, task: dict, result, timing: dict) -> None:
    """Запись результата задачи в БД, затем подтверждение ``task_done``.

    Каталог: по вызову ``apply_catalog`` на каждый артикул (транзакция на
    артикул; повторная запись после переотдачи идемпотентна), ``db`` — словарь
    «артикул → статистика БД». Item: ``apply_item``, ``db`` — статистика БД.

    В ``task_done`` уходит ``{"db": …, "timing": …}``: ``timing`` несёт
    ``started_at`` (wall-clock ISO взятия задачи), ``parse_ms`` (длительность
    парсинга), ``write_ms`` (длительность записи) и ``total_ms`` (старт →
    конец записи, включая ожидание в очереди)."""
    loop = asyncio.get_event_loop()
    params = task["params"]
    t_w0 = loop.time()
    if task["type"] == "catalog":
        db = {}
        for article, cat in result.per_query.items():
            db[article] = await store.apply_catalog(
                article, cat,
                zip=params["zip"], condition=params.get("condition"),
                min_price=params.get("min_price"), max_price=params.get("max_price"),
            )
    else:
        db = await store.apply_item(result, zip=params["zip"])
    t_w1 = loop.time()
    timing["write_ms"] = round((t_w1 - t_w0) * 1000)
    timing["total_ms"] = round((t_w1 - timing.pop("_t_start")) * 1000)
    stats = {"db": db, "timing": timing}
    logger.debug("written %s: %.200s", task.get("type"), stats)
    if task_done is not None:
        await task_done(task, stats)


async def run_worker(
    get_page,
    next_task,
    store: Store,
    *,
    task_done=None,
    page_delay_s: float = PAGE_DELAY_S,
) -> None:
    """Цикл воркера: ``next_task`` → парсинг (``EbaySession``) → очередь →
    запись (``Store``) → ``task_done``.

    Возвращается только при ``next_task() -> None`` (дописав хвост очереди);
    любая ошибка — исключение наружу, воркер умирает целиком. ``store``
    закрывается при любом исходе. Сессия и страницы живут весь цикл; замены
    страниц при блокировках — внутри сессии, незаметно для цикла."""
    session = EbaySession(get_page, page_delay_s=page_delay_s)
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    loop = asyncio.get_event_loop()

    async def writer() -> None:
        while True:
            got = await queue.get()
            if got is _STOP:
                return
            task, result, timing = got
            try:
                await _write_result(store, task_done, task, result, timing)
            except BaseException as e:
                _blame(e, task, "write failed")
                raise

    writer_task = asyncio.create_task(writer())

    async def race(coro):
        """Ждёт ``coro``; смерть писателя прерывает ожидание его исключением —
        иначе при сломанной БД воркер вечно висел бы на ``next_task``."""
        t = asyncio.ensure_future(coro)
        done, _ = await asyncio.wait({t, writer_task}, return_when=asyncio.FIRST_COMPLETED)
        if t in done:
            return t.result()
        t.cancel()
        with suppress(asyncio.CancelledError):
            await t
        writer_task.result()  # пробрасывает исключение писателя
        raise RuntimeError("writer exited unexpectedly")  # _STOP ещё не клали

    async def finish_writer() -> None:
        """Кладёт _STOP (не зависая, если писатель уже мёртв) и ждёт, пока
        писатель допишет хвост очереди; его ошибка летит наружу."""
        put = asyncio.ensure_future(queue.put(_STOP))
        await asyncio.wait({put, writer_task}, return_when=asyncio.FIRST_COMPLETED)
        if not put.done():
            put.cancel()
            with suppress(asyncio.CancelledError):
                await put
        await writer_task

    try:
        try:
            while True:
                task = None
                task = await race(next_task())
                if task is None:
                    logger.debug("next_task -> None, finishing")
                    break
                logger.debug("task: %.200s", task)
                started_at = datetime.now(timezone.utc).isoformat()
                t_start = loop.time()
                result = await race(_dispatch(session, task))
                timing = {"started_at": started_at, "_t_start": t_start,
                          "parse_ms": round((loop.time() - t_start) * 1000)}
                await race(queue.put((task, result, timing)))
        except BaseException as e:
            # Виновница в лог и на исключение (если её ещё не назвал писатель;
            # сбой next_task — задачи нет, только лог типа ошибки).
            if getattr(e, "task", None) is None and task is not None:
                _blame(e, task, "worker dying")
            # Ошибка парсинга/next_task (или писателя — через race): дописываем
            # уже готовый хвост очереди, затем умираем с исходной ошибкой.
            try:
                await finish_writer()
            except Exception:
                logger.exception("writer failed while flushing the tail")
            raise
        # Штатное завершение: дописать хвост; ошибка записи здесь — наружу.
        await finish_writer()
    finally:
        try:
            await store.close()
        except Exception:
            logger.exception("store.close() failed")
