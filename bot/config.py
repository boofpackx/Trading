"""Strategy and account configuration.

All times are US/Eastern. The defaults encode the agreed spec:
NY AM session (entries 9:30-11:00, flat by 15:55), 5m structure / 1m entry,
$250 risk per trade, min 1:1 reward-to-risk, OTE (61.8-79%) entries,
SMT divergence NQ vs ES, Topstep 50K guardrails (stop at 2 losses or -$500).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    point_value: float  # dollars per index point per contract
    tick_size: float


NQ = InstrumentSpec("NQ", 20.0, 0.25)
MNQ = InstrumentSpec("MNQ", 2.0, 0.25)
ES = InstrumentSpec("ES", 50.0, 0.25)


@dataclass
class Settings:
    # --- session (ET) ---
    entry_window_start: time = time(9, 30)
    entry_window_end: time = time(11, 0)
    flat_by: time = time(15, 55)

    # --- timeframes (seconds) ---
    structure_tf: int = 300  # 5m structure
    entry_tf: int = 60       # 1m entries

    # --- risk ---
    risk_per_trade: float = 250.0
    min_rr: float = 1.0
    stop_buffer_ticks: int = 4  # stop placed this many ticks beyond the leg extreme

    # --- daily guardrails (Topstep 50K: $1,000 DLL / $2,000 MLL) ---
    max_daily_losses: int = 2
    max_daily_drawdown: float = 500.0
    max_minis: int = 5
    max_micros: int = 50

    # --- strategy ---
    swing_lookback: int = 2          # fractal width on 5m structure
    ote_low: float = 0.618           # OTE retracement zone bounds
    ote_high: float = 0.79
    ote_entry: float = 0.705         # sweet-spot limit price within the zone
    smt_window: int = 30             # 1m candles examined for SMT divergence

    # --- runtime ---
    mode: str = field(default_factory=lambda: os.getenv("BOT_MODE", "sim"))
    projectx_url: str = field(
        default_factory=lambda: os.getenv("PROJECTX_API_URL", "https://api.topstepx.com")
    )
    projectx_username: str = field(default_factory=lambda: os.getenv("PROJECTX_USERNAME", ""))
    projectx_api_key: str = field(default_factory=lambda: os.getenv("PROJECTX_API_KEY", ""))
    projectx_account_id: str = field(default_factory=lambda: os.getenv("PROJECTX_ACCOUNT_ID", ""))


SETTINGS = Settings()
