"""Core datatypes shared across the bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


@dataclass
class Candle:
    ts: datetime  # open time, tz-aware ET
    open: float
    high: float
    low: float
    close: float

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)


class SwingKind(str, Enum):
    HIGH = "high"
    LOW = "low"


@dataclass
class Swing:
    index: int  # index into the candle series it was derived from
    ts: datetime
    price: float
    kind: SwingKind
    swept: bool = False
    swept_at: Optional[datetime] = None


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class SetupState(str, Enum):
    AWAITING_RETRACE = "awaiting_retrace"  # leg formed, waiting for pullback into OTE
    READY = "ready"                        # in OTE + SMT confirmed, staged for user confirm
    CONFIRMED = "confirmed"                # user approved, order submitted
    FILLED = "filled"
    SKIPPED = "skipped"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


@dataclass
class Setup:
    id: int
    direction: Direction
    created: datetime
    leg_start: float   # origin extreme of the impulse leg (sweep point)
    leg_end: float     # terminal extreme of the impulse leg
    entry: float       # OTE sweet-spot (70.5%) limit price
    stop: float
    target: float      # nearest internal high/low (draw on liquidity)
    ote_zone: tuple[float, float]  # (upper, lower) prices of the 61.8-79% band
    rr: float
    symbol: str        # NQ or MNQ after sizing
    contracts: int
    dollar_risk: float
    smt_note: str
    state: SetupState = SetupState.AWAITING_RETRACE
    note: str = ""


@dataclass
class Position:
    setup_id: int
    direction: Direction
    symbol: str
    contracts: int
    entry: float
    stop: float
    target: float
    point_value: float
    opened: datetime
    unrealized: float = 0.0


@dataclass
class TradeResult:
    setup_id: int
    direction: Direction
    symbol: str
    contracts: int
    entry: float
    exit: float
    pnl: float
    reason: str  # target | stop | flat_close | manual
    closed: datetime


@dataclass
class DayStats:
    pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    trades: list[TradeResult] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""
