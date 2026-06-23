"""Тест каталог-воркера (ebay_library.worker.run_catalog_worker) — офлайн, без браузера.

CatalogSession подменяем FakeSession (fetch_catalog отдаёт канон CatalogResult, дёргает
on_fetch), store — FakeStore. Проверяем: батчи обрабатываются, запись на КАЖДЫЙ артикул,
task_done строго после записи всех, и смерть «по жёсткому» (критика валит пул наружу).
⚠️ Логика на моке — не гарантия живого поведения. Запуск:
``PYTHONPATH=. python3 tests/test_catalog_worker.py``.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import ebay_library.browser.catalog_session as CSM
import ebay_library.worker as W
from ebay_library.errors import ParseError
from ebay_library.models import Catalog, CatalogItem, CatalogResult


class FakeStore:
    def __init__(self):
        self.calls = []

    async def apply_catalog(self, article, catalog, *, zip, condition=None,
                            min_price=None, max_price=None):
        self.calls.append((article, len(catalog.items)))
        return {"ok": True, "article": article}


class FakeSession:
    """Подмена CatalogSession: fetch_catalog отдаёт по 1 карточке на артикул и дёргает
    on_fetch; raise_on — артикул, на котором падаем ParseError (по жёсткому)."""

    raise_on = None

    def __init__(self, get_page, *, controller=None, on_fetch=None):
        self._on_fetch = on_fetch
        self.epoch = 0

    async def start(self):
        return None

    async def fetch_catalog(self, queries, *, zip, condition=None, min_price=None,
                            max_price=None, max_pages=5):
        await asyncio.sleep(0)
        qs = queries if isinstance(queries, list) else [queries]
        per_query = {}
        for a in qs:
            if self._on_fetch:
                self._on_fetch(100.0)                  # per-fetch ms
            if a == FakeSession.raise_on:
                raise ParseError("seller", None, a, "<html/>")
            card = CatalogItem(item_id="1" * 12, title="x", condition="new", price=1.0,
                               shipping_cost=0.0, seller="s", location=None, image_url="u")
            per_query[a] = Catalog(query=a, results_count=1, items=[card],
                                   pages_fetched=1, has_fewer_words_sep=False)
        items = [c for cat in per_query.values() for c in cat.items]
        return CatalogResult(items=items, per_query=per_query)


async def _get_page():
    return object()


def _run(coro):
    return asyncio.run(coro)


def test_processes_batches_and_writes_each_article():
    FakeSession.raise_on = None
    CSM.CatalogSession = FakeSession

    async def scenario():
        store = FakeStore()
        batches = [{"articles": ["A", "B"]}, {"articles": ["C"]}, {"articles": ["D", "E"]}]
        it = iter(batches); lock = asyncio.Lock()
        done = []
        finished = asyncio.Event()

        async def next_task():
            async with lock:
                return next(it, None)

        async def task_done(task, stats):
            done.append((task, stats))
            if len(done) == 3:
                finished.set()

        w = asyncio.create_task(W.run_catalog_worker(
            _get_page, next_task, task_done, store, start_c=4))
        await asyncio.wait_for(finished.wait(), timeout=5)
        w.cancel()
        with suppress(asyncio.CancelledError):
            await w
        return store, done

    store, done = _run(scenario())
    assert len(done) == 3, done
    assert sorted(a for a, _ in store.calls) == ["A", "B", "C", "D", "E"], store.calls
    # стат содержит per_article по всем артикулам батча
    arts_done = sorted(d["article"] for _, st in done for d in st["per_article"])
    assert arts_done == ["A", "B", "C", "D", "E"], arts_done


def test_critical_kills_pool():
    FakeSession.raise_on = "BAD"
    CSM.CatalogSession = FakeSession

    async def scenario():
        store = FakeStore()
        batches = [{"articles": ["BAD"]}]
        it = iter(batches); lock = asyncio.Lock()

        async def next_task():
            async with lock:
                return next(it, None)

        async def task_done(task, stats):
            pass

        await W.run_catalog_worker(_get_page, next_task, task_done, store, start_c=2)

    try:
        _run(scenario())
    except ParseError as e:
        assert e.field == "seller"
    else:
        raise AssertionError("ParseError (по жёсткому) ожидался наружу")
    finally:
        FakeSession.raise_on = None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
