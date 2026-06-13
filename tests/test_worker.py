"""T9 — run_worker: цикл «задача → парсинг → очередь → запись → task_done».
Прогон: PYTHONPATH=. python3 tests/test_worker.py

Фейковые страницы — из test_session (реальные фикстуры); Store — фейковый
(duck-typed, записывает вызовы). Сеть нужна только fx-эндпоинту (каталог).

Покрытие:
  - happy-поток: catalog + item; порядок записи; task_done после записи,
    со статистикой; None завершает; store закрыт;
  - ошибка парсинга → смерть, но готовый хвост очереди дописан (цельность);
  - ошибка записи → быстрая смерть, даже когда парсинг висит на next_task;
  - кривые задачи (тип/params) → TaskFormatError без обращения к браузеру.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_session import (
    ERRPAGE, HOME, ITEM, ITEM_ENDED, ITEM_OK, SRP, SRP_10, FakePage, feeder,
)

from ebaylib import ErrorPageError, TaskFormatError, run_worker

CATALOG_TASK = {"type": "catalog", "id": 1,
                "params": {"articles": "8M0142836", "zip": "19701"}}
ITEM_TASK = {"type": "item", "id": 2,
             "params": {"item_id": "277574984378", "zip": "19701"}}


class FakeStore:
    def __init__(self, *, delay_s=0.0, fail=False):
        self.calls = []
        self.closed = False
        self._delay_s = delay_s
        self._fail = fail

    async def apply_catalog(self, article, catalog, *, zip, condition=None,
                            min_price=None, max_price=None):
        if self._fail:
            raise RuntimeError("db down")
        await asyncio.sleep(self._delay_s)
        self.calls.append(("catalog", article, len(catalog.items), zip, condition))
        return {"fetch_id": len(self.calls), "items_total": len(catalog.items)}

    async def apply_item(self, item, *, zip):
        await asyncio.sleep(self._delay_s)
        self.calls.append(("item", item.item_number, zip))
        return {"item_id": int(item.item_number), "is_new": True}

    async def apply_item_ended(self, item_id):
        await asyncio.sleep(self._delay_s)
        self.calls.append(("ended", item_id))
        return {"item_id": int(item_id), "was_new": False, "dead_reason": "ended"}

    async def close(self):
        self.closed = True


def tasks_source(tasks):
    it = iter(tasks)
    async def next_task():
        return next(it, None)
    return next_task


def no_pages():
    async def get_page():
        raise AssertionError("get_page must not be called")
    return get_page


def test_happy_flow():
    pages = [FakePage("A", [HOME, SRP(SRP_10), ITEM(ITEM_OK)])]
    get_page, calls = feeder(pages)
    store = FakeStore()
    done = []

    async def task_done(task, stats):
        # подтверждение строго после записи: вызов уже лежит в store.calls
        done.append((task["id"], stats, len(store.calls)))

    asyncio.run(run_worker(get_page, tasks_source([CATALOG_TASK, ITEM_TASK]),
                           store, task_done=task_done, page_delay_s=0.05))
    assert calls == ["A"], calls
    assert [c[0] for c in store.calls] == ["catalog", "item"], store.calls
    assert store.calls[0][1] == "8M0142836" and store.calls[0][2] == 10
    assert store.calls[1][1] == "277574984378"
    # task_done: после записи; stats = {"db": …, "timing": …}; каталог — db по артикулам
    assert [d[0] for d in done] == [1, 2]
    assert done[0][1]["db"] == {"8M0142836": {"fetch_id": 1, "items_total": 10}}, done[0]
    assert done[0][2] >= 1 and done[1][2] == 2, done
    assert done[1][1]["db"]["item_id"] == 277574984378
    # тайминги: started_at (ISO), total_ms, residual_ms, поэтапный stages
    tm = done[0][1]["timing"]
    assert set(tm) == {"started_at", "total_ms", "residual_ms", "stages"}, tm
    assert "T" in tm["started_at"]
    # каталог: полный набор ключей этапов (включая нули)
    assert set(tm["stages"]) == {"swap", "nav", "ready", "parse", "fx", "queue", "write"}
    # item-задача: вместо fx — desc
    assert set(done[1][1]["timing"]["stages"]) == {
        "swap", "nav", "ready", "desc", "parse", "queue", "write"}
    # самопроверка: total ≈ сумма этапов (residual ~0, дробные мс)
    assert abs(tm["residual_ms"]) < 1.0, tm["residual_ms"]
    assert abs(tm["total_ms"] - sum(tm["stages"].values())) < 1.0
    assert store.closed


def test_ended_item_writes_via_apply_item_ended():
    # ENDED-страница → fetch_item возвращает ItemEnded → запись через
    # apply_item_ended, task_done со stats
    pages = [FakePage("A", [HOME, ITEM_ENDED])]
    get_page, _ = feeder(pages)
    store = FakeStore()
    done = []

    async def task_done(task, stats):
        done.append((task["id"], stats["db"]))

    task = {"type": "item", "id": 7, "params": {"item_id": "205404777715", "zip": "19701"}}
    asyncio.run(run_worker(get_page, tasks_source([task]),
                           store, task_done=task_done, page_delay_s=0.05))
    assert store.calls == [("ended", "205404777715")], store.calls
    assert done == [(7, {"item_id": 205404777715, "was_new": False, "dead_reason": "ended"})], done
    assert store.closed


def test_parse_error_flushes_tail():
    # задача 1 ок (запись медленная — результат ещё в полёте), задача 2 — Error Page
    pages = [FakePage("A", [HOME, SRP(SRP_10), ERRPAGE])]
    get_page, _ = feeder(pages)
    store = FakeStore(delay_s=0.3)
    done = []

    async def task_done(task, stats):
        done.append(task["id"])

    task2 = {"type": "catalog", "id": 99, "params": {"articles": "x", "zip": "19701"}}
    try:
        asyncio.run(run_worker(get_page, tasks_source([CATALOG_TASK, task2]),
                               store, task_done=task_done, page_delay_s=0.05))
    except ErrorPageError as e:
        assert e.task is task2, getattr(e, "task", None)  # виновница приложена
    else:
        raise AssertionError("expected ErrorPageError")
    # хвост дописан перед смертью: результат задачи 1 в БД, подтверждение было
    assert [c[0] for c in store.calls] == ["catalog"], store.calls
    assert done == [1], done
    assert store.closed


def test_write_error_kills_promptly():
    # запись падает; парсинг после первой задачи виснет на next_task навечно —
    # race обязан прервать ожидание ошибкой писателя (быстро, не таймаутом)
    pages = [FakePage("A", [HOME, SRP(SRP_10)])]
    get_page, _ = feeder(pages)
    store = FakeStore(fail=True)

    async def next_task_hangs():
        if not hasattr(next_task_hangs, "given"):
            next_task_hangs.given = True
            return CATALOG_TASK
        await asyncio.Event().wait()  # навечно

    async def main():
        await asyncio.wait_for(
            run_worker(get_page, next_task_hangs, store, page_delay_s=0.05),
            timeout=10,
        )

    try:
        asyncio.run(main())
    except RuntimeError as e:
        assert "db down" in str(e), e
        assert e.task is CATALOG_TASK, getattr(e, "task", None)  # задача, которая писалась
    else:
        raise AssertionError("expected RuntimeError('db down')")
    assert store.closed


def test_bad_tasks_die_without_browser():
    for bad in (
        {"type": "weird", "params": {"zip": "1"}},          # неизвестный тип
        {"type": "catalog", "params": {"articles": "x"}},   # нет zip
        {"type": "item", "params": {"zip": "19701"}},       # нет item_id
        {"type": "catalog"},                                # нет params
        "not-a-dict",
    ):
        store = FakeStore()
        try:
            asyncio.run(run_worker(no_pages(), tasks_source([bad]), store))
        except TaskFormatError:
            pass
        else:
            raise AssertionError(f"expected TaskFormatError for {bad!r}")
        assert store.calls == [] and store.closed


def test_none_exits_cleanly():
    store = FakeStore()
    asyncio.run(run_worker(no_pages(), tasks_source([]), store))
    assert store.calls == [] and store.closed


if __name__ == "__main__":
    test_happy_flow()
    test_ended_item_writes_via_apply_item_ended()
    test_parse_error_flushes_tail()
    test_write_error_kills_promptly()
    test_bad_tasks_die_without_browser()
    test_none_exits_cleanly()
    print("PASS")
