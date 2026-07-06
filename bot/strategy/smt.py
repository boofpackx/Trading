"""SMT divergence between the Nasdaq (NQ) and S&P 500 (ES).

The two indices normally move together. When one makes a new extreme and the
other refuses to confirm it, the crack in correlation ("Smart Money
Technique" divergence) marks the raid as likely engineered:

- Bullish SMT: NQ takes out a prior low while ES holds above its own
  corresponding low (or vice versa).
- Bearish SMT: one index takes out a prior high while the other fails to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import Candle, Direction


@dataclass
class SMTResult:
    divergent: bool
    note: str


def _recent_extremes(candles: list[Candle], window: int) -> tuple[list[Candle], list[Candle]]:
    """Split the last `window` candles in half: (older half, newer half).
    The older half sets the reference extreme; the newer half shows whether
    it was taken out."""
    recent = candles[-window:]
    mid = len(recent) // 2
    older, newer = recent[:mid], recent[mid:]
    return older, newer


def check_smt(
    a: list[Candle],
    b: list[Candle],
    direction: Direction,
    window: int = 30,
    a_name: str = "NQ",
    b_name: str = "ES",
) -> SMTResult:
    """Compare the two correlated series over the last `window` entry-timeframe
    candles. For a LONG we want bullish SMT at the low: one index broke its
    prior low, the other held."""
    if len(a) < window or len(b) < window:
        return SMTResult(False, "insufficient data for SMT")

    a_old, a_new = _recent_extremes(a, window)
    b_old, b_new = _recent_extremes(b, window)

    if direction is Direction.LONG:
        a_broke = min(c.low for c in a_new) < min(c.low for c in a_old)
        b_broke = min(c.low for c in b_new) < min(c.low for c in b_old)
        if a_broke != b_broke:
            breaker, holder = (a_name, b_name) if a_broke else (b_name, a_name)
            return SMTResult(
                True, f"bullish SMT: {breaker} took the low, {holder} held"
            )
        return SMTResult(False, "no SMT: both indices moved in sync at the low")

    a_broke = max(c.high for c in a_new) > max(c.high for c in a_old)
    b_broke = max(c.high for c in b_new) > max(c.high for c in b_old)
    if a_broke != b_broke:
        breaker, holder = (a_name, b_name) if a_broke else (b_name, a_name)
        return SMTResult(
            True, f"bearish SMT: {breaker} took the high, {holder} held"
        )
    return SMTResult(False, "no SMT: both indices moved in sync at the high")
