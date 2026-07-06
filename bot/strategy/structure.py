"""Market structure: fractal swings, liquidity sweeps, internal highs/lows.

"Internal" liquidity here means 5m swing points inside the session's dealing
range that price has not yet traded through. Unswept swing highs are buy-side
liquidity (targets for longs); unswept swing lows are sell-side liquidity
(targets for shorts).
"""

from __future__ import annotations

from typing import Optional

from ..models import Candle, Swing, SwingKind


def find_swings(candles: list[Candle], lookback: int = 2) -> list[Swing]:
    """Fractal swing detection: a swing high/low is strictly the extreme of the
    `lookback` candles on each side. Sweep status is then marked from the
    candles that follow each swing."""
    swings: list[Swing] = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        c = candles[i]
        window = candles[i - lookback : i + lookback + 1]
        if c.high == max(w.high for w in window) and sum(
            1 for w in window if w.high == c.high
        ) == 1:
            swings.append(Swing(i, c.ts, c.high, SwingKind.HIGH))
        if c.low == min(w.low for w in window) and sum(
            1 for w in window if w.low == c.low
        ) == 1:
            swings.append(Swing(i, c.ts, c.low, SwingKind.LOW))
    mark_sweeps(swings, candles)
    return swings


def mark_sweeps(swings: list[Swing], candles: list[Candle]) -> None:
    """A swing high is swept once a later candle trades above it; a swing low
    once a later candle trades below it."""
    for s in swings:
        for c in candles[s.index + 1 :]:
            if s.kind is SwingKind.HIGH and c.high > s.price:
                s.swept, s.swept_at = True, c.ts
                break
            if s.kind is SwingKind.LOW and c.low < s.price:
                s.swept, s.swept_at = True, c.ts
                break


def nearest_internal_high(swings: list[Swing], above: float) -> Optional[Swing]:
    """Nearest unswept swing high strictly above `above` — the draw on
    liquidity for a long."""
    candidates = [
        s for s in swings if s.kind is SwingKind.HIGH and not s.swept and s.price > above
    ]
    return min(candidates, key=lambda s: s.price) if candidates else None


def nearest_internal_low(swings: list[Swing], below: float) -> Optional[Swing]:
    """Nearest unswept swing low strictly below `below` — the draw on
    liquidity for a short."""
    candidates = [
        s for s in swings if s.kind is SwingKind.LOW and not s.swept and s.price < below
    ]
    return max(candidates, key=lambda s: s.price) if candidates else None


def last_sweep(
    swings: list[Swing], candles: list[Candle], kind: SwingKind
) -> Optional[tuple[Swing, int]]:
    """Most recent liquidity sweep of the given kind with a close-back-through
    (rejection). Returns (swept swing, index of the sweeping candle).

    For a swing low: some later candle's low trades below the swing price but
    that candle (or a following one, before price runs away) closes back above
    it — a raid on sell-side liquidity, the seed of a long.
    """
    best: Optional[tuple[Swing, int]] = None
    for s in swings:
        if s.kind is not kind or not s.swept:
            continue
        for j in range(s.index + 1, len(candles)):
            c = candles[j]
            if kind is SwingKind.LOW and c.low < s.price and c.close > s.price:
                if best is None or j > best[1]:
                    best = (s, j)
                break
            if kind is SwingKind.HIGH and c.high > s.price and c.close < s.price:
                if best is None or j > best[1]:
                    best = (s, j)
                break
            # price ran away without rejecting: not a sweep-and-reject
            if kind is SwingKind.LOW and c.close < s.price:
                break
            if kind is SwingKind.HIGH and c.close > s.price:
                break
    return best


def structure_shift_up(candles: list[Candle], since: int) -> bool:
    """Displacement after a sell-side sweep at candle index `since`: a later
    close above the sweep candle's high — the raid was rejected with force."""
    ref = candles[since].high
    return any(c.close > ref for c in candles[since + 1 :])


def structure_shift_down(candles: list[Candle], since: int) -> bool:
    """Displacement after a buy-side sweep: a later close below the sweep
    candle's low."""
    ref = candles[since].low
    return any(c.close < ref for c in candles[since + 1 :])
