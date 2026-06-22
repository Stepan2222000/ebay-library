"""Адаптивный контроллер конкуренции (этап 4, SPEC.md §8). Чистая политика, без IO:
подбирает ``C`` (число запросов в полёте), нащупывая «колено» по латентности. Общий
для обоих лейнов (item / каталог); семафор и сбор метрик — в воркере (этапы 6/9).

⚠️  ПРЕДВАРИТЕЛЬНЫЙ, под тюнинг живьём (SPEC.md §8.4). Объёмный бой на сервере вскрыл
коллапс ПЕРВОЙ версии (baseline = абсолютный min латентности): при шумной реальной
латентности ebaydesc (p50 скачет 1.5–14с) min пинился на lucky-low сэмпле, почти
каждое окно казалось «насыщением» (>lat_rise×baseline) → C схлопывался к 1 (≈60/мин),
тогда как фикс-C=30 даёт 980/мин. ИСПРАВЛЕНО здесь двумя приёмами (всё ещё под живой
тюнинг — overshoot возможен, потолок ``ceiling`` воркера — предохранитель):
  1. baseline = **скользящий минимум окна** последних p50 (старые lucky-low стареют,
     baseline не пинится навечно на одном выбросе);
  2. срез C — только при **устойчивом насыщении** (``sat_windows`` подряд), а не на
     одном шумном окне (гистерезис).

Алгоритм (словами). Воркер раз в ОКНО (пачка из N завершённых запросов) считает
throughput (запросов/сек), latency_p50 (медиана), degraded (ошибки/Pardon/таймауты) и
зовёт ``update(...)``:
  • degraded → ``C × backoff`` (жёсткий ответ на перегрузку), история сброшена;
  • иначе: baseline = min недавних p50; throughput сглаживаем в ``ema``; затем
      – устойчивое насыщение (``latency_p50 ≥ baseline×lat_rise`` ``sat_windows`` окон
        подряд) → режем ``C`` (страховка по латентности, закон Литтла);
      – иначе throughput ещё растёт (``ema > prev×(1+eps)``) → растим ``C``;
      – иначе (плато) → ДЕРЖИМ ``C``.
Главный рычаг стабильности — РАЗМЕР ОКНА (большое = меньше шум), задаётся воркером.
"""

from __future__ import annotations

from collections import deque


class Controller:
    """Состояние и решения адаптивной конкуренции. ``C`` — публичное: воркер читает
    его как целевой лимит семафора. Не потокобезопасен (один на воркер/лейн)."""

    def __init__(self, *, start: int = 4, min_c: int = 1, step: float = 0.25,
                 lat_rise: float = 1.8, eps: float = 0.05, smooth: float = 0.4,
                 backoff: float = 0.6, sat_windows: int = 2, baseline_window: int = 15):
        # Все пороги — ПРЕДВАРИТЕЛЬНЫЕ дефолты (см. шапку), под тюнинг живьём.
        self.C = start
        self._start = start
        self._min = min_c
        self._step = step              # доля роста/среза C за шаг (+25%)
        self._lat_rise = lat_rise      # во сколько p50 > baseline = «насыщение» (×1.8)
        self._eps = eps                # порог роста throughput, «ещё растём» (+5%)
        self._smooth = smooth          # коэффициент EMA throughput (гасит шум окна)
        self._backoff = backoff        # во сколько режем C при degraded/Pardon (×0.6)
        self._sat_windows = sat_windows  # сколько окон подряд насыщения до среза (гистерезис)
        self._lats: deque[float] = deque(maxlen=baseline_window)  # окно p50 → робастный baseline
        self._ema = None               # сглаженный throughput
        self._prev = None              # ema на прошлом окне (для детекта роста)
        self._sat = 0                  # счётчик подряд насыщенных окон

    def _grow(self) -> int:
        return self.C + max(1, int(self.C * self._step))

    def _shrink(self) -> int:
        return max(self._min, self.C - max(1, int(self.C * self._step)))

    def update(self, throughput: float, latency_p50: float, degraded: bool = False) -> int:
        """Решение после окна → новый ``C``. ``degraded`` — были ошибки/Pardon/таймауты."""
        if degraded:
            self.C = max(self._min, int(self.C * self._backoff))
            self._ema = self._prev = None
            self._sat = 0
            return self.C
        self._lats.append(latency_p50)
        baseline = min(self._lats)              # скользящий минимум — робастно к выбросам-низам
        self._ema = (throughput if self._ema is None
                     else self._smooth * throughput + (1 - self._smooth) * self._ema)
        saturated = latency_p50 >= baseline * self._lat_rise
        grew = self._prev is None or self._ema > self._prev * (1 + self._eps)
        if saturated:
            self._sat += 1
            if self._sat >= self._sat_windows:  # только УСТОЙЧИВОЕ насыщение → назад
                self.C = self._shrink()
                self._sat = 0
        else:
            self._sat = 0
            if grew:                            # добавление C ещё ускоряет → разгон
                self.C = self._grow()
            # else: плато (роста нет, латентность ок) → держим C
        self._prev = self._ema
        return self.C

    def backoff_now(self) -> int:
        """Мгновенный ``C × backoff`` — Pardon мид-окно (каталог, SPEC.md §7.3)."""
        self.C = max(self._min, int(self.C * self._backoff))
        self._ema = self._prev = None
        self._sat = 0
        return self.C

    def reset(self) -> int:
        """``C`` → старт, забыть baseline/ema — смена страницы (новый прокси =
        неизвестная ёмкость, учимся заново; каталог, SPEC.md §7.3)."""
        self.C = self._start
        self._lats.clear()
        self._ema = self._prev = None
        self._sat = 0
        return self.C
