"""Тест адаптивного контроллера (ebay_library.concurrency) — офлайн, детерминированно.

Юнит-проверки кормят точные последовательности (throughput, latency); прогон по
модели очереди (seeded) проверяет отслеживание колена и падения ёмкости. ⚠️ Это
проверка ЛОГИКИ на модели — НЕ гарантия живого поведения (см. шапку concurrency.py).

Запуск: ``PYTHONPATH=. python3 tests/test_concurrency.py``.
"""

from __future__ import annotations

import random

from ebay_library.concurrency import Controller


def test_ramps_while_throughput_grows():
    c = Controller(start=4)
    start = c.C
    for tput in (100, 160, 240, 360, 520):     # растущий throughput, латентность ровная
        c.update(tput, 0.10)
    assert c.C > start, c.C                     # разогнался


def test_holds_on_plateau():
    c = Controller(start=4)
    c.update(100, 0.10)                         # один разгон (prev пуст)
    seq = [c.update(500, 0.10) for _ in range(6)]  # throughput встал, латентность ровная
    assert seq[-1] == seq[-3], seq             # держит (не растёт и не режет)


def test_shrinks_on_sustained_high_latency():
    c = Controller(start=8)
    c.update(100, 0.10); c.update(100, 0.10)    # baseline = 0.10
    before = c.C
    c.update(100, 0.30)                          # одно окно насыщения — НЕ режем (гистерезис)
    assert c.C == before, (before, c.C)
    c.update(100, 0.30)                          # второе подряд → срез
    assert c.C < before, (before, c.C)


def test_degraded_backoff():
    c = Controller(start=10, backoff=0.6)
    c.update(500, 0.10, degraded=True)
    assert c.C == 6, c.C                        # 10 × 0.6


def test_backoff_now_and_reset():
    c = Controller(start=4)
    for tput in (100, 200, 400, 700):
        c.update(tput, 0.10)
    ramped = c.C
    assert ramped > 4
    assert c.backoff_now() < ramped
    assert c.reset() == 4                       # назад к старту


def test_never_below_min():
    c = Controller(start=2, min_c=1)
    for _ in range(10):
        c.backoff_now()
    assert c.C >= 1, c.C


def _measure(C, K, base=0.10):
    """Модель очереди: C≤K — латентность ровная, throughput растёт; C>K — очередь."""
    if C <= K:
        t, l = C / base, base
    else:
        t, l = K / base, base * C / K
    return t * (1 + random.uniform(-0.02, 0.02)), l * (1 + random.uniform(-0.02, 0.02))


def test_tracks_knee_then_capacity_drop():
    random.seed(7)
    c = Controller()
    for _ in range(40):
        t, l = _measure(c.C, 50)
        c.update(t, l)
    assert 40 <= c.C <= 95, c.C                 # осел у/выше колена 50 (throughput на максимуме)
    settled = c.C
    for _ in range(25):                         # ёмкость упала: K 50 → 15
        t, l = _measure(c.C, 15)
        c.update(t, l)
    # отступил существенно к новому колену (гистерезис тормозит down-ответ — это ок)
    assert c.C < settled * 0.6, (settled, c.C)


def test_no_collapse_under_noisy_latency():
    """Регресс на прод-баг: при ШУМНОЙ латентности (тяжёлый хвост) старый контроллер
    схлопывал C→1; новый (скользящий-min baseline + гистерезис) — не должен."""
    import statistics
    random.seed(3)
    c = Controller(start=8)
    K, BASE = 40, 1.0
    for _ in range(70):
        cong = 1.0 if c.C <= K else c.C / K
        cold = 1.6 if c.C <= 4 else 1.0                      # cold-штраф у низкого C
        lats = [BASE * cong * cold * random.lognormvariate(0, 0.6) for _ in range(64)]
        c.update(c.C / statistics.mean(lats), statistics.median(lats))
    assert c.C > 8, c.C                                      # разогнался, НЕ схлопнулся к 1


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
