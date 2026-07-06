"""Fibonacci retracement / Optimal Trade Entry (OTE).

The OTE zone is the 61.8%-79% retracement of an impulse leg, with the 70.5%
level as the sweet-spot entry. For a bullish leg drawn low -> high, the fib is
anchored 0 at the leg high and 1 at the leg low, so a 70.5% retracement sits
70.5% of the way back down the leg.
"""

from __future__ import annotations

from ..models import Direction


def retracement_level(leg_start: float, leg_end: float, ratio: float) -> float:
    """Price at `ratio` retracement of the leg (leg_start = origin extreme,
    leg_end = terminal extreme). ratio=0 -> leg_end, ratio=1 -> leg_start."""
    return leg_end - (leg_end - leg_start) * ratio


def ote_zone(
    leg_start: float, leg_end: float, low: float = 0.618, high: float = 0.79
) -> tuple[float, float]:
    """(shallow_price, deep_price) bounds of the OTE band. For a long the
    shallow bound (61.8%) is above the deep bound (79%); for a short it is
    below. Callers treat the pair as an unordered band."""
    return (
        retracement_level(leg_start, leg_end, low),
        retracement_level(leg_start, leg_end, high),
    )


def ote_entry_price(
    leg_start: float, leg_end: float, ratio: float = 0.705, tick: float = 0.25
) -> float:
    price = retracement_level(leg_start, leg_end, ratio)
    return round(price / tick) * tick


def in_zone(price: float, zone: tuple[float, float]) -> bool:
    lo, hi = min(zone), max(zone)
    return lo <= price <= hi


def zone_touched(candle_low: float, candle_high: float, zone: tuple[float, float]) -> bool:
    lo, hi = min(zone), max(zone)
    return candle_high >= lo and candle_low <= hi


def leg_valid(direction: Direction, price: float, leg_start: float) -> bool:
    """The setup dies if price trades through the origin of the leg."""
    if direction is Direction.LONG:
        return price > leg_start
    return price < leg_start
