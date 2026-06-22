"""Тест item-воркера (ebay_library.worker.run_item_worker) — офлайн, на моках.

Сеть/БД подменяем: ``fetch_item_page`` и ``make_item_session`` — моки (мгновенно),
``store``/``next_task``/``task_done`` — заглушки. Проверяем: все задачи обработаны
(task_done со стадиями), критическая ошибка валит воркер (по жёсткому), `None`=ждём
(воркер не завершается). Логику масштабирования отдельно гоняли на мок-нагрузке
(этап 6, прототип).

Запуск: ``PYTHONPATH=. python3 tests/test_item_worker.py``.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import ebay_library.worker as W
from ebay_library.errors import ParseError
from ebay_library.models import ItemPage


def _dummy_item(iid: str = "1") -> ItemPage:
    return ItemPage(item_number=iid, title="t", condition="new", price_usd=None,
                    shipping_cost=None, seller="s", location="l",
                    specifics={"B": "x"}, image_urls=[], description="", last_updated=None)


class _DummySession:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


def _patch(monkey_fetch):
    W.make_item_session = lambda **kw: _DummySession()
    W.fetch_item_page = monkey_fetch


class _MockStore:
    def __init__(self): self.applied = []
    async def apply_item(self, item, *, zip): self.applied.append(item.item_number); return {"ok": 1}


def _supply(n):
    state = {"i": 0}
    lock = asyncio.Lock()
    async def next_task():
        async with lock:
            if state["i"] < n:
                state["i"] += 1
                return {"item_id": str(state["i"])}
            return None
    return next_task


def test_processes_all_tasks():
    async def ok_fetch(session, item_id, *, prof=None):
        if prof:
            prof.switch("fetch"); prof.switch("desc"); prof.switch("parse")
        return _dummy_item(item_id)
    _patch(ok_fetch)
    store = _MockStore(); done = []
    async def task_done(task, stats): done.append((task, stats))

    async def scenario():
        wt = asyncio.create_task(W.run_item_worker(
            _supply(20), task_done, store, start_c=4, window=8, max_window_s=0.5))
        loop = asyncio.get_event_loop(); t0 = loop.time()
        while len(done) < 20 and loop.time() - t0 < 5:
            await asyncio.sleep(0.01)
        wt.cancel()
        with suppress(asyncio.CancelledError):
            await wt
    asyncio.run(scenario())

    assert len(done) == 20, len(done)
    assert sorted(store.applied, key=int) == [str(i) for i in range(1, 21)], store.applied
    _, stats = done[0]
    assert "fetch" in stats["timing"]["stages"], stats
    assert "write" in stats["timing"]["stages"], stats


def test_critical_error_kills_worker():
    async def bad_fetch(session, item_id, *, prof=None):
        raise ParseError("title", None, item_id, "<html/>")
    _patch(bad_fetch)
    store = _MockStore()
    async def task_done(task, stats): pass

    raised = None
    try:
        asyncio.run(W.run_item_worker(_supply(5), task_done, store, start_c=2))
    except ParseError as e:
        raised = e
    assert raised is not None, "ParseError ожидался (воркер должен упасть)"
    assert getattr(raised, "task", None) is not None  # виновница приложена


def test_none_means_wait_not_exit():
    async def ok_fetch(session, item_id, *, prof=None): return _dummy_item(item_id)
    _patch(ok_fetch)
    store = _MockStore(); done = []
    async def task_done(task, stats): done.append(task)

    async def scenario():
        wt = asyncio.create_task(W.run_item_worker(_supply(3), task_done, store, start_c=2))
        loop = asyncio.get_event_loop(); t0 = loop.time()
        while len(done) < 3 and loop.time() - t0 < 3:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.4)               # next_task->None: воркер должен ЖИТЬ
        alive = not wt.done()
        wt.cancel()
        with suppress(asyncio.CancelledError):
            await wt
        return alive
    alive = asyncio.run(scenario())
    assert len(done) == 3
    assert alive, "воркер не должен завершаться на None (None=ждём)"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
