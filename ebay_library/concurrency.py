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


class CatalogController:
    """Адаптивная конкуренция для КАТАЛОГА (этап 9, SPEC.md §8). Устроена ИНАЧЕ, чем
    item-``Controller``, потому что предел конкуренции у лейнов разный: у item —
    латентность сети (нет блокировок), у каталога — **пропускная способность канала и
    блокировки** (антибот). Высокая латентность у каталога — это 3МБ-страницы, а НЕ
    перегрузка, поэтому латентность здесь **не уменьшает** ``C``.

    Логика (раз в окно воркер зовёт ``update(throughput, latency_p50)``):
      • throughput ещё растёт И латентность не взлетела (канал не насыщен) → растим ``C``;
      • плато (throughput встал ЛИБО ``p50 ≥ baseline×lat_stop``) → ДЕРЖИМ — латентность
        лишь ОСТАНАВЛИВАЕТ рост, сама ``C`` не режет;
      • throughput ПРОСЕЛ (прокси деградировал) → мягкий спад ``C``;
      • антибот → ``backoff_now()`` (Pardon) / ``reset()`` (Access Denied) — их зовёт
        сама ``CatalogSession`` на общем контроллере (SPEC.md §7.3), не петля окна.

    ⚠️ ПРЕДВАРИТЕЛЬНАЯ, под живой тюнинг (SPEC.md §8.4): живьём подтверждено, что вкладка
    параллелит fetch до плато канала (~8–11/сек на прокси) без антибота; пороги ниже —
    стартовые. Потолка ``C`` нет (контроллер сам встаёт на плато)."""

    def __init__(self, *, start: int = 8, min_c: int = 2, step: float = 0.2,
                 eps: float = 0.05, smooth: float = 0.4, drop: float = 0.15,
                 lat_stop: float = 2.0, backoff: float = 0.6, baseline_window: int = 15,
                 plateau_window: int = 2):
        self.C = start
        self._start = start
        self._min = min_c
        self._step = step              # доля роста/спада C за шаг (+20%)
        self._eps = eps                # порог роста throughput, «ещё растём» (+5%)
        self._smooth = smooth          # EMA throughput (гасит шум окна)
        self._drop = drop              # просадка throughput (−15%) → мягкий спад
        self._lat_stop = lat_stop      # p50 ≥ baseline×lat_stop → стоп-РОСТА (не срез)
        self._backoff = backoff        # во сколько режем C на Pardon/degraded (×0.6)
        self._pw = plateau_window      # сколько окон назад сравниваем throughput (анти-лаг)
        self._lats: deque[float] = deque(maxlen=baseline_window)  # окно p50 → baseline (мин)
        self._ema = None
        # история EMA: рост признаём, только если throughput выше, чем _pw окон назад —
        # иначе лаг EMA тянет «рост» далеко за плато (переразгон в латентный ад).
        self._ema_hist: deque[float] = deque(maxlen=plateau_window)

    def update(self, throughput: float, latency_p50: float, degraded: bool = False) -> int:
        """Решение после окна → новый ``C``. ``degraded`` (ошибки/Pardon в окне) → backoff.
        Латентность только ограничивает рост, не уменьшает ``C`` (см. шапку)."""
        if degraded:
            return self.backoff_now()
        self._lats.append(latency_p50)
        baseline = min(self._lats)
        self._ema = (throughput if self._ema is None
                     else self._smooth * throughput + (1 - self._smooth) * self._ema)
        ref = self._ema_hist[0] if len(self._ema_hist) >= self._pw else None  # EMA _pw окон назад
        if ref is not None and self._ema < ref * (1 - self._drop):
            # throughput просел против _pw окон назад (прокси деградировал) → мягкий спад
            self.C = max(self._min, self.C - max(1, int(self.C * self._step * 0.5)))
        else:
            rising = ref is None or self._ema > ref * (1 + self._eps)
            lat_ok = latency_p50 < baseline * self._lat_stop
            if rising and lat_ok:                       # канал ещё тянет → разгон
                self.C = self.C + max(1, int(self.C * self._step))
            # else: плато (throughput не растёт _pw окон или латентность взлетела) → держим
        self._ema_hist.append(self._ema)
        return self.C

    def backoff_now(self) -> int:
        """``C × backoff`` — Pardon/degraded (каталог, SPEC.md §7.3)."""
        self.C = max(self._min, int(self.C * self._backoff))
        self._ema = None
        self._ema_hist.clear()
        return self.C

    def reset(self) -> int:
        """``C`` → старт, забыть историю — смена страницы (Access Denied → новый прокси =
        неизвестная ёмкость, SPEC.md §7.3)."""
        self.C = self._start
        self._lats.clear()
        self._ema = None
        self._ema_hist.clear()
        return self.C
