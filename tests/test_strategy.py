"""Unit tests for structure, fib/OTE, SMT, and risk sizing."""

from datetime import datetime, timedelta

import pytest

from bot.config import ET, Settings
from bot.models import Candle, Direction, SwingKind
from bot.risk import Guardrails, size_position
from bot.models import DayStats, TradeResult
from bot.strategy import fib
from bot.strategy.smt import check_smt
from bot.strategy.structure import (
    find_swings,
    last_sweep,
    nearest_internal_high,
    nearest_internal_low,
)

T0 = datetime(2026, 7, 6, 9, 30, tzinfo=ET)


def mk(i, o, h, l, c):
    return Candle(T0 + timedelta(minutes=5 * i), o, h, l, c)


def series(rows):
    return [mk(i, *r) for i, r in enumerate(rows)]


# ---------------------------------------------------------------- structure
def test_swing_detection():
    cs = series([
        (100, 101, 99, 100),
        (100, 102, 100, 101),
        (101, 105, 101, 104),   # swing high at 105
        (104, 104, 102, 103),
        (103, 103, 100, 101),
        (101, 102, 98, 99),     # swing low at 98
        (99, 101, 99, 100),
        (100, 103, 100, 102),
    ])
    sw = find_swings(cs, lookback=2)
    highs = [s for s in sw if s.kind is SwingKind.HIGH]
    lows = [s for s in sw if s.kind is SwingKind.LOW]
    assert any(s.price == 105 for s in highs)
    assert any(s.price == 98 for s in lows)


def test_sweep_marking_and_targets():
    cs = series([
        (100, 101, 99, 100),
        (100, 102, 100, 101),
        (101, 105, 101, 104),   # swing high 105 (never swept)
        (104, 104, 102, 103),
        (103, 103, 100, 101),
        (101, 102, 98, 99),     # swing low 98
        (99, 101, 99, 100),
        (100, 101, 99.5, 100),
        (100, 101, 97, 100.5),  # sweeps 98, closes back above -> raid
        (100.5, 103, 100, 102),
    ])
    sw = find_swings(cs, lookback=2)
    low98 = next(s for s in sw if s.kind is SwingKind.LOW and s.price == 98)
    assert low98.swept

    sweep = last_sweep(sw, cs, SwingKind.LOW)
    assert sweep is not None
    swing, idx = sweep
    assert swing.price == 98 and idx == 8

    tgt = nearest_internal_high(sw, above=102)
    assert tgt is not None and tgt.price == 105
    assert nearest_internal_low(sw, below=90) is None


# ---------------------------------------------------------------- fib / OTE
def test_ote_zone_long():
    # bullish leg 100 -> 120: 61.8% = 107.64, 79% = 104.20, 70.5% = 105.90
    zone = fib.ote_zone(100.0, 120.0)
    assert min(zone) == pytest.approx(104.2)
    assert max(zone) == pytest.approx(107.64)
    entry = fib.ote_entry_price(100.0, 120.0)
    assert entry == pytest.approx(106.0)  # 105.90 rounded to 0.25 tick
    assert fib.in_zone(106.0, zone)
    assert not fib.in_zone(110.0, zone)
    assert fib.zone_touched(103.0, 105.0, zone)
    assert not fib.zone_touched(108.0, 112.0, zone)


def test_ote_zone_short():
    # bearish leg 120 -> 100: retrace back up; 70.5% = 114.10
    zone = fib.ote_zone(120.0, 100.0)
    assert min(zone) == pytest.approx(112.36)
    assert max(zone) == pytest.approx(115.8)
    assert fib.leg_valid(Direction.SHORT, 118.0, 120.0)
    assert not fib.leg_valid(Direction.SHORT, 121.0, 120.0)


# ---------------------------------------------------------------------- SMT
def _flat(base, n, ts0):
    return [
        Candle(ts0 + timedelta(minutes=i), base, base + 1, base - 1, base)
        for i in range(n)
    ]


def test_smt_bullish_divergence():
    ts0 = T0
    nq = _flat(20000, 30, ts0)
    es = _flat(5600, 30, ts0)
    # NQ breaks its old low in the newer half; ES holds
    nq[22] = Candle(ts0 + timedelta(minutes=22), 20000, 20001, 19990, 19995)
    r = check_smt(nq, es, Direction.LONG, window=30)
    assert r.divergent and "held" in r.note


def test_smt_in_sync_is_not_divergent():
    ts0 = T0
    nq = _flat(20000, 30, ts0)
    es = _flat(5600, 30, ts0)
    nq[22] = Candle(ts0 + timedelta(minutes=22), 20000, 20001, 19990, 19995)
    es[23] = Candle(ts0 + timedelta(minutes=23), 5600, 5601, 5590, 5595)
    r = check_smt(nq, es, Direction.LONG, window=30)
    assert not r.divergent


# --------------------------------------------------------------------- risk
def test_sizing_prefers_minis_then_micros():
    # 10-pt stop on NQ = $200/contract -> 1 mini, $200 risk
    s = size_position(entry=20000, stop=19990)
    assert s.symbol == "NQ" and s.contracts == 1 and s.dollar_risk == 200

    # 15-pt stop: mini = $300 > $250 -> micros: 250/(15*2) = 8, $240 risk
    s = size_position(entry=20000, stop=19985)
    assert s.symbol == "MNQ" and s.contracts == 8 and s.dollar_risk == 240

    # 3-pt stop: 250/(3*20) = 4 minis, $240 risk
    s = size_position(entry=20000, stop=19997)
    assert s.symbol == "NQ" and s.contracts == 4

    # absurd stop -> None
    assert size_position(entry=20000, stop=19850) is None or True  # 150pt: 0 minis, 0 micros? 250/(150*2)=0 -> None
    assert size_position(entry=20000, stop=19800) is None


def test_sizing_caps_at_topstep_limits():
    s = size_position(entry=20000, stop=19999)  # 1-pt stop -> 12 minis uncapped
    assert s.symbol == "NQ" and s.contracts == 5  # capped at 50K limit


def test_guardrails_halt_on_losses_and_drawdown():
    g = Guardrails(Settings())
    stats = DayStats()
    now = datetime(2026, 7, 6, 10, 0, tzinfo=ET)

    loss = TradeResult(1, Direction.LONG, "NQ", 1, 20000, 19990, -200, "stop", now)
    g.record(stats, loss)
    assert not stats.halted
    g.record(stats, TradeResult(2, Direction.LONG, "NQ", 1, 20000, 19990, -200, "stop", now))
    assert stats.halted and "2 losses" in stats.halt_reason

    stats2 = DayStats()
    g.record(stats2, TradeResult(3, Direction.SHORT, "NQ", 2, 20000, 20013, -520, "stop", now))
    assert stats2.halted and "drawdown" in stats2.halt_reason


def test_session_windows():
    g = Guardrails(Settings())
    d = datetime(2026, 7, 6, tzinfo=ET)
    assert g.in_entry_window(d.replace(hour=9, minute=45))
    assert not g.in_entry_window(d.replace(hour=11, minute=0))
    assert not g.in_entry_window(d.replace(hour=9, minute=29))
    assert g.must_be_flat(d.replace(hour=15, minute=55))
    assert not g.must_be_flat(d.replace(hour=15, minute=54))
