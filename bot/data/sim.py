"""Simulated correlated NQ/ES feed for paper trading and demoing the UI.

Generates a shared market factor plus per-index noise so the two series are
highly correlated but occasionally diverge — which is exactly what produces
liquidity sweeps and SMT divergences for the engine to find. The sim clock
starts at 9:28 ET "today" and each real second advances one simulated minute,
so a full AM session plays out in a few minutes of watching.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Optional

from ..config import ET
from ..models import Candle


class SimFeed:
    def __init__(self, seed: Optional[int] = None, nq_start: float = 20000.0, es_start: float = 5600.0):
        self.rng = random.Random(seed)
        # start pre-market so 5m structure already exists at the 9:30 open
        base = datetime.now(ET).replace(hour=8, minute=15, second=0, microsecond=0)
        self.clock = base
        self.nq_price = nq_start
        self.es_price = es_start
        self.nq_1m: list[Candle] = []
        self.es_1m: list[Candle] = []
        # regime state: drift flips occasionally to create legs and retraces
        self._drift = 0.0
        self._drift_left = 0
        # occasional decoupling so NQ/ES can print SMT divergences
        self._es_bias = 0.0

    def _step_regime(self):
        if self._drift_left <= 0:
            was_impulse = abs(self._drift) >= 3.0
            r = self.rng.random()
            if was_impulse and r < 0.7:
                # retrace: lean back against the impulse — this is what pulls
                # price into the OTE band of the leg just printed
                self._drift = (
                    -(1 if self._drift > 0 else -1) * self.rng.uniform(1.0, 3.0)
                )
                self._drift_left = self.rng.randint(4, 9)
            elif r < 0.4:
                self._drift = self.rng.choice([-1, 1]) * self.rng.uniform(3.0, 8.0)
                self._drift_left = self.rng.randint(4, 10)
            elif r < 0.65:
                self._drift = self.rng.choice([-1, 1]) * self.rng.uniform(0.5, 2.0)
                self._drift_left = self.rng.randint(6, 15)
            else:
                self._drift = 0.0
                self._drift_left = self.rng.randint(5, 12)
            # ~60% of regimes, ES leans against the shared move a little —
            # enough for one index to hold a low/high the other takes out
            self._es_bias = (
                -self._drift * self.rng.uniform(0.15, 0.45)
                if self.rng.random() < 0.6
                else 0.0
            )
        self._drift_left -= 1

    def _make_candle(self, price: float, drift: float, vol: float, tick: float = 0.25):
        o = price
        c = o + drift + self.rng.gauss(0, vol)
        wick = abs(self.rng.gauss(0, vol * 0.8))
        h = max(o, c) + wick
        l = min(o, c) - abs(self.rng.gauss(0, vol * 0.8))
        rnd = lambda x: round(x / tick) * tick
        return rnd(o), rnd(h), rnd(l), rnd(c)

    def next_minute(self) -> tuple[Candle, Candle]:
        """Advance one simulated minute; returns the closed (NQ, ES) candles."""
        self._step_regime()
        shared = self._drift + self.rng.gauss(0, 4.0)

        nq_move = shared * 1.0 + self.rng.gauss(0, 3.0)
        es_move = shared * 0.26 + self._es_bias + self.rng.gauss(0, 0.9)  # ES ~ quarter of NQ's point moves

        o, h, l, c = self._make_candle(self.nq_price, nq_move, 4.0)
        nq = Candle(self.clock, o, h, l, c)
        self.nq_price = c

        o, h, l, c = self._make_candle(self.es_price, es_move, 1.1)
        es = Candle(self.clock, o, h, l, c)
        self.es_price = c

        self.nq_1m.append(nq)
        self.es_1m.append(es)
        self.clock += timedelta(minutes=1)
        return nq, es
