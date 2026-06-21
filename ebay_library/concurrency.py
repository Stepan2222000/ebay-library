"""Адаптивный контроллер конкуренции (этап 4, SPEC.md §8). Чистая политика, без IO:
подбирает ``C`` (число запросов в полёте), нащупывая «колено» по латентности. Общий
для обоих лейнов (item / каталог); семафор и сбор метрик — в воркере (этапы 6/9).

⚠️  ВАЖНО: алгоритм ПРЕДВАРИТЕЛЬНЫЙ — не факт, что оптимальный (зафиксировано с этой
пометкой по решению пользователя). Обкатан ТОЛЬКО на синтетической модели очереди
(разгон, плато-холд, backoff, reset, отслеживание смены ёмкости K↓/K↑ — ок), но **НЕ
на живом трафике**. Пороги (``step``/``lat_rise``/``eps``/``smooth``/``backoff``),
размер окна (его обеспечивает воркер) и сама форма решения — кандидаты на
пересмотр/тюнинг живьём (SPEC.md §8.4). Цифры §8 (C≈100) — из прошлого каталог-теста,
не из этого контроллера. Подключать к воркеру с готовностью переделать.

Алгоритм (словами). Воркер раз в ОКНО (пачка из N завершённых запросов) считает
throughput (запросов/сек), latency_p50 (медиана, мерится внутри запроса), degraded
(были ошибки/Pardon/таймауты) и зовёт ``update(...)``:
  • degraded → ``C × backoff`` (жёсткий ответ на перегрузку), история сброшена;
  • иначе: ``baseline`` = min виденной латентности; throughput сглаживаем в ``ema``
    (гасит шум окна — иначе ложные разгоны у плато); затем
      – латентность поползла (``latency_p50 ≥ baseline × lat_rise``) → режем ``C``
        (страховка, закон Литтла: за коленом рост C лишь растит очередь);
      – иначе throughput ещё растёт (``ema > prev × (1+eps)``) → растим ``C``;
      – иначе (плато, латентность ок) → ДЕРЖИМ ``C``.
Верхнего софт-потолка нет — предел задаёт колено латентности. Главный рычаг
стабильности — РАЗМЕР ОКНА (большое окно = меньше шум = ровнее), задаётся воркером.
"""

from __future__ import annotations


class Controller:
    """Состояние и решения адаптивной конкуренции. ``C`` — публичное: воркер читает
    его как целевой лимит семафора. Не потокобезопасен (один на воркер/лейн)."""

    def __init__(self, *, start: int = 4, min_c: int = 1, step: float = 0.25,
                 lat_rise: float = 1.6, eps: float = 0.05, smooth: float = 0.4,
                 backoff: float = 0.6):
        # Все пороги — ПРЕДВАРИТЕЛЬНЫЕ дефолты (см. шапку), под тюнинг живьём.
        self.C = start
        self._start = start
        self._min = min_c
        self._step = step          # доля роста/среза C за шаг (+25%)
        self._lat_rise = lat_rise  # во сколько латентность > baseline = «насыщение» (×1.6)
        self._eps = eps            # порог роста throughput, чтобы счесть «ещё растём» (+5%)
        self._smooth = smooth      # коэффициент EMA throughput (гасит шум окна)
        self._backoff = backoff    # во сколько режем C при degraded/Pardon (×0.6)
        self._baseline = None      # минимальная виденная латентность
        self._ema = None           # сглаженный throughput
        self._prev = None          # ema на прошлом окне (для детекта роста)

    def _grow(self) -> int:
        return self.C + max(1, int(self.C * self._step))

    def _shrink(self) -> int:
        return max(self._min, self.C - max(1, int(self.C * self._step)))

    def update(self, throughput: float, latency_p50: float, degraded: bool = False) -> int:
        """Решение после окна → новый ``C``. ``degraded`` — были ошибки/Pardon/таймауты."""
        if degraded:
            self.C = max(self._min, int(self.C * self._backoff))
            self._ema = self._prev = None        # эталоны throughput сброшены
            return self.C
        if self._baseline is None or latency_p50 < self._baseline:
            self._baseline = latency_p50
        self._ema = (throughput if self._ema is None
                     else self._smooth * throughput + (1 - self._smooth) * self._ema)
        saturated = latency_p50 >= self._baseline * self._lat_rise
        grew = self._prev is None or self._ema > self._prev * (1 + self._eps)
        if saturated:                # латентность поползла → назад (страховка)
            self.C = self._shrink()
        elif grew:                   # добавление C ещё ускоряет → разгон
            self.C = self._grow()
        # else: плато (роста нет, латентность ок) → держим C
        self._prev = self._ema
        return self.C

    def backoff_now(self) -> int:
        """Мгновенный ``C × backoff`` — Pardon мид-окно (каталог, SPEC.md §7.3)."""
        self.C = max(self._min, int(self.C * self._backoff))
        self._ema = self._prev = None
        return self.C

    def reset(self) -> int:
        """``C`` → старт, забыть baseline/ema — смена страницы (новый прокси =
        неизвестная ёмкость, учимся заново; каталог, SPEC.md §7.3)."""
        self.C = self._start
        self._baseline = self._ema = self._prev = None
        return self.C
