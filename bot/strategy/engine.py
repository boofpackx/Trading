"""Setup detection state machine.

The model, per ICT's NY AM playbook, long side (shorts mirrored):

1. On 5m structure: price sweeps an internal (unswept) swing low and closes
   back above it — a raid on sell-side liquidity.
2. The sweep is followed by displacement: a later 5m close above the sweep
   candle's high. The impulse leg is drawn from the sweep extreme to the
   running high of that displacement.
3. Fib the leg: wait for a 1m retracement into the OTE band (61.8-79%).
4. SMT filter: NQ vs ES must show divergence at the raid.
5. Take profit: in "fixed" mode the target lands 30-50 points from entry
   (the internal swing level is used when it falls inside that band, else
   the distance is clamped to it); in "internal" mode it sits exactly at the
   nearest unswept internal 5m swing. Stop goes a few ticks beyond the sweep
   extreme; reward must be >= min_rr * risk after sizing to $250.

A qualifying setup is emitted as READY and staged for one-click confirmation;
it invalidates if price trades through the leg origin first.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from ..config import SETTINGS, NQ, Settings
from ..models import Candle, Direction, Setup, SetupState, SwingKind
from ..risk import size_position
from . import fib
from .smt import check_smt
from .structure import (
    find_swings,
    last_sweep,
    nearest_internal_high,
    nearest_internal_low,
    structure_shift_down,
    structure_shift_up,
)


def resolve_target(
    entry: float,
    level_price: Optional[float],
    direction: Direction,
    settings: Settings,
    tick: float = NQ.tick_size,
) -> Optional[float]:
    """Take-profit price per settings.target_mode (see module docstring)."""
    sign = 1 if direction is Direction.LONG else -1
    if settings.target_mode == "internal":
        return level_price  # may be None -> no trade

    lo, hi = settings.fixed_target_min, settings.fixed_target_max
    if level_price is None:
        dist = (lo + hi) / 2
    else:
        dist = min(max((level_price - entry) * sign, lo), hi)
    price = entry + sign * dist
    return round(price / tick) * tick


class StrategyEngine:
    """Feed it closed 5m and 1m candles for NQ (and 1m for ES); it returns a
    staged Setup when everything lines up."""

    def __init__(self, settings: Settings = SETTINGS, log: Optional[Callable[[str], None]] = None):
        self.s = settings
        self.log = log or (lambda _msg: None)
        self._setup_seq = 0
        self._last_logged_leg: Optional[tuple] = None
        self.pending: Optional[Setup] = None  # AWAITING_RETRACE or READY

    # ------------------------------------------------------------------ 5m
    def on_structure_candle(self, nq_5m: list[Candle]) -> None:
        """Called after each 5m close: look for a fresh sweep + displacement
        and (re)draw the impulse leg."""
        if len(nq_5m) < self.s.swing_lookback * 2 + 3:
            return
        swings = find_swings(nq_5m, self.s.swing_lookback)
        if self.pending and self.pending.state is SetupState.READY:
            return  # a staged setup owns the state until confirmed/invalidated

        candidate = self._detect_leg(nq_5m, swings, Direction.LONG)
        if candidate is None:
            candidate = self._detect_leg(nq_5m, swings, Direction.SHORT)
        if candidate is not None:
            leg_key = (candidate.direction, candidate.leg_start, candidate.leg_end)
            if leg_key != self._last_logged_leg:
                self._last_logged_leg = leg_key
                self.log(
                    f"impulse leg {candidate.direction.value}: "
                    f"{candidate.leg_start:.2f} -> {candidate.leg_end:.2f}, "
                    f"awaiting OTE retrace"
                )
            self.pending = candidate

    def _detect_leg(
        self, candles: list[Candle], swings, direction: Direction
    ) -> Optional[Setup]:
        sweep_kind = SwingKind.LOW if direction is Direction.LONG else SwingKind.HIGH
        sweep = last_sweep(swings, candles, sweep_kind)
        if sweep is None:
            return None
        swing, sweep_idx = sweep

        shifted = (
            structure_shift_up(candles, sweep_idx)
            if direction is Direction.LONG
            else structure_shift_down(candles, sweep_idx)
        )
        if not shifted:
            return None

        since = candles[sweep_idx:]
        if direction is Direction.LONG:
            leg_start = min(c.low for c in since)  # the sweep extreme
            leg_end = max(c.high for c in since)
        else:
            leg_start = max(c.high for c in since)
            leg_end = min(c.low for c in since)
        if abs(leg_end - leg_start) < NQ.tick_size * 8:
            return None  # no displacement worth trading

        zone = fib.ote_zone(leg_start, leg_end, self.s.ote_low, self.s.ote_high)
        entry = fib.ote_entry_price(leg_start, leg_end, self.s.ote_entry, NQ.tick_size)
        buffer = self.s.stop_buffer_ticks * NQ.tick_size
        stop = leg_start - buffer if direction is Direction.LONG else leg_start + buffer

        target_swing = (
            nearest_internal_high(swings, leg_end)
            if direction is Direction.LONG
            else nearest_internal_low(swings, leg_end)
        )
        target = resolve_target(
            entry,
            target_swing.price if target_swing else None,
            direction,
            self.s,
        )
        if target is None:
            return None  # internal mode with no liquidity left to draw on

        risk_pts = abs(entry - stop)
        reward_pts = abs(target - entry)
        if risk_pts <= 0 or reward_pts / risk_pts < self.s.min_rr:
            return None

        sized = size_position(entry, stop, self.s)
        if sized is None:
            return None

        self._setup_seq += 1
        return Setup(
            id=self._setup_seq,
            direction=direction,
            created=candles[-1].ts,
            leg_start=leg_start,
            leg_end=leg_end,
            entry=entry,
            stop=stop,
            target=target,
            ote_zone=zone,
            rr=round(reward_pts / risk_pts, 2),
            symbol=sized.symbol,
            contracts=sized.contracts,
            dollar_risk=round(sized.dollar_risk, 2),
            smt_note="",
            state=SetupState.AWAITING_RETRACE,
        )

    # ------------------------------------------------------------------ 1m
    def on_entry_candle(
        self, nq_1m: list[Candle], es_1m: list[Candle], now: datetime
    ) -> Optional[Setup]:
        """Called after each 1m close. Returns the Setup when it flips to
        READY (price tagged the OTE band with SMT divergence in place)."""
        p = self.pending
        if p is None or p.state is not SetupState.AWAITING_RETRACE or not nq_1m:
            return None
        last = nq_1m[-1]

        if not fib.leg_valid(p.direction, last.close, p.leg_start):
            p.state = SetupState.INVALIDATED
            self.log(f"setup #{p.id} invalidated: price traded through the leg origin")
            self.pending = None
            return None

        if not fib.zone_touched(last.low, last.high, p.ote_zone):
            return None

        smt = check_smt(nq_1m, es_1m, p.direction, self.s.smt_window)
        if not smt.divergent:
            self.log(f"OTE touched but no SMT divergence — standing down ({smt.note})")
            return None

        p.smt_note = smt.note
        p.state = SetupState.READY
        p.note = (
            f"OTE tagged at {last.close:.2f}; {smt.note}; "
            f"{p.contracts} {p.symbol} risking ${p.dollar_risk:.0f} for {p.rr}R"
        )
        self.log(f"SETUP READY #{p.id}: {p.direction.value} {p.note}")
        return p

    def clear(self) -> None:
        self.pending = None
