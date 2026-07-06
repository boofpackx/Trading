"""Position sizing and Topstep guardrails.

Sizing targets a fixed $250 dollar risk. It prefers NQ minis ($20/pt) and
falls back to MNQ micros ($2/pt) when a single mini would already exceed the
risk budget. Contract counts respect Topstep 50K scaling limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

from .config import MNQ, NQ, SETTINGS, Settings
from .models import DayStats, TradeResult


@dataclass
class SizedOrder:
    symbol: str
    contracts: int
    point_value: float
    dollar_risk: float


def size_position(
    entry: float, stop: float, settings: Settings = SETTINGS
) -> Optional[SizedOrder]:
    stop_points = abs(entry - stop)
    if stop_points <= 0:
        return None

    risk = settings.risk_per_trade
    minis = int(risk // (stop_points * NQ.point_value))
    if minis >= 1:
        minis = min(minis, settings.max_minis)
        return SizedOrder(NQ.symbol, minis, NQ.point_value, minis * stop_points * NQ.point_value)

    micros = int(risk // (stop_points * MNQ.point_value))
    if micros >= 1:
        micros = min(micros, settings.max_micros)
        return SizedOrder(MNQ.symbol, micros, MNQ.point_value, micros * stop_points * MNQ.point_value)

    return None  # stop too wide even for a single micro


class Guardrails:
    """Daily circuit breakers: N losses or -$X on the day halts new entries.
    Session windows gate when entries are allowed and when everything must be
    flat (Topstep requires flat before the 4:10pm ET close; we use 3:55pm)."""

    def __init__(self, settings: Settings = SETTINGS):
        self.s = settings

    def record(self, stats: DayStats, result: TradeResult) -> None:
        stats.pnl += result.pnl
        stats.trades.append(result)
        if result.pnl < 0:
            stats.losses += 1
        elif result.pnl > 0:
            stats.wins += 1
        self.refresh_halt(stats)

    def refresh_halt(self, stats: DayStats) -> None:
        if stats.losses >= self.s.max_daily_losses:
            stats.halted = True
            stats.halt_reason = f"{stats.losses} losses — done for the day"
        elif stats.pnl <= -self.s.max_daily_drawdown:
            stats.halted = True
            stats.halt_reason = (
                f"daily drawdown ${-stats.pnl:.0f} hit the ${self.s.max_daily_drawdown:.0f} stop"
            )

    def in_entry_window(self, now_et: datetime) -> bool:
        t = now_et.time()
        return self.s.entry_window_start <= t < self.s.entry_window_end

    def must_be_flat(self, now_et: datetime) -> bool:
        return now_et.time() >= self.s.flat_by

    def can_enter(self, stats: DayStats, now_et: datetime) -> tuple[bool, str]:
        if stats.halted:
            return False, stats.halt_reason
        if not self.in_entry_window(now_et):
            return False, "outside 9:30-11:00 ET entry window"
        return True, ""
