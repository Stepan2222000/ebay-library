"""Тест каталог-контроллера (ebay_library.concurrency.CatalogController) — офлайн.

Каталог: предел — канал/блокировки, НЕ латентность. Ключевое отличие от item-Controller:
высокая латентность (3МБ-страницы) НЕ уменьшает C — только останавливает рост. Срез —
только просадка throughput или антибот (backoff_now/reset). ⚠️ Проверка ЛОГИКИ на модели,
не гарантия живого поведения (SPEC.md §8.4). Запуск: ``PYTHONPATH=. python3 tests/test_catalog_controller.py``.
"""

from __future__ import annotations

from ebay_library.concurrency import CatalogController


def test_ramps_while_throughput_grows():
    c = CatalogController(start=8)
    start = c.C
    for tput in (100, 160, 240, 360, 520):     # throughput растёт, латентность ровная
        c.update(tput, 2.0)
    assert c.C > start, c.C


def test_high_but_stable_latency_does_not_shrink():
    """Главное отличие от item: высокая латентность при НЕрастущем throughput → ДЕРЖИМ,
    НЕ режем (item-Controller тут бы срезал)."""
    c = CatalogController(start=20)
    c.update(500, 2.0)                          # baseline ~2с, один разгон
    held = c.C
    for _ in range(4):
        c.update(500, 9.0)                      # латентность скакнула 9с (>2.5×baseline), throughput ровный
    assert c.C == held, (held, c.C)             # держим: не выросли (lat стоп) и не срезали


def test_holds_on_throughput_plateau():
    c = CatalogController(start=20)
    c.update(500, 2.0)
    seq = [c.update(500, 2.0) for _ in range(5)]   # throughput встал, латентность ровная
    assert seq[-1] == seq[-3], seq              # держит


def test_shrinks_on_throughput_drop():
    c = CatalogController(start=20)
    c.update(500, 2.0); c.update(500, 2.0)      # ema ~500
    before = c.C
    c.update(300, 2.0)                           # throughput просел −40% (прокси деградировал) → спад
    assert c.C < before, (before, c.C)


def test_backoff_and_reset():
    c = CatalogController(start=8, backoff=0.6)
    for tput in (100, 200, 400, 800):
        c.update(tput, 2.0)
    ramped = c.C
    assert ramped > 8
    assert c.backoff_now() < ramped             # Pardon → ×0.6
    c2 = CatalogController(start=8)
    for tput in (100, 200, 400):
        c2.update(tput, 2.0)
    assert c2.reset() == 8                       # Access Denied → к старту


def test_degraded_triggers_backoff():
    c = CatalogController(start=20, backoff=0.6)
    c.update(500, 2.0)
    before = c.C
    assert c.update(500, 2.0, degraded=True) < before


def _channel(C, knee=80, unit=0.13, base=3.0):
    """Модель канала по живым данным (этап 9): C≤knee — throughput ∝ C (растёт),
    латентность ровная; C>knee — throughput на плато, латентность пухнет КРУТО
    (≈(C/knee)^1.6, как живьём: K80→4.4с, K120→6.6с, K160→10с)."""
    if C <= knee:
        return C * unit, base
    return knee * unit, base * (C / knee) ** 1.6


def test_settles_near_channel_knee_no_runaway():
    """Контроллер должен НАЙТИ высокий C (канал), но НЕ убежать в латентный ад: крутая
    латентность за коленом включает стоп-роста. Точная посадка — под live-тюнинг §8.4;
    тест гарантирует разгон выше колена + ограниченность (без бесконечного runaway)."""
    c = CatalogController(start=8)
    traj = []
    for _ in range(60):
        t, l = _channel(c.C)
        c.update(t, l)
        traj.append(c.C)
    assert max(traj) >= 80, traj[-5:]            # разогнался до/выше колена
    assert c.C <= 170, c.C                        # не убежал (lat_stop ограничил)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
