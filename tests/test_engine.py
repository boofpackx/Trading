"""End-to-end engine test: a scripted sweep -> displacement -> OTE retrace
with SMT divergence must produce a READY long setup with correct levels."""

from datetime import datetime, timedelta

from bot.config import ET, Settings
from bot.models import Candle, Direction, SetupState
from bot.strategy.engine import StrategyEngine, resolve_target

T0 = datetime(2026, 7, 6, 9, 30, tzinfo=ET)


def c5(i, o, h, l, c):
    return Candle(T0 + timedelta(minutes=5 * i), o, h, l, c)


def c1(i, o, h, l, c):
    return Candle(T0 + timedelta(minutes=i), o, h, l, c)


def build_5m():
    """5m tape: range with a swing low at 19980 and swing highs at 20040 and
    20090 (unswept, the target), then a sweep of 19980 and displacement up
    through 20040."""
    return [
        c5(0, 20000, 20010, 19990, 20005),
        c5(1, 20005, 20020, 20000, 20015),
        c5(2, 20015, 20040, 20010, 20030),  # swing high 20040
        c5(3, 20030, 20035, 20000, 20005),
        c5(4, 20005, 20010, 19980, 19990),  # swing low 19980
        c5(5, 19990, 20005, 19985, 20000),
        c5(6, 20000, 20010, 19990, 20005),
        c5(7, 20005, 20090, 20000, 20085),  # swing-high candidate; runs to 20090
        c5(8, 20085, 20088, 20050, 20060),
        c5(9, 20060, 20070, 20040, 20050),
        c5(10, 20050, 20055, 19970, 19995),  # SWEEP of 19980, close back above
        c5(11, 19995, 20065, 19990, 20060),  # displacement: close above sweep candle high
    ]


def test_engine_full_long_setup():
    s = Settings()
    s.smt_window = 10
    eng = StrategyEngine(s)

    eng.on_structure_candle(build_5m())
    p = eng.pending
    assert p is not None, "engine should have drawn an impulse leg"
    assert p.direction is Direction.LONG
    assert p.leg_start == 19970.0  # the sweep extreme
    assert p.leg_end == 20065.0
    assert p.stop == 19970.0 - 1.0  # 4 ticks * 0.25 buffer
    # internal high (20090) is 92 pts from entry -> clamped to the 50-pt cap
    assert p.target == p.entry + 50.0
    assert p.rr >= s.min_rr
    assert p.state is SetupState.AWAITING_RETRACE

    # 1m tape: drift down into the OTE zone; NQ breaks a recent low, ES holds
    zone_lo, zone_hi = min(p.ote_zone), max(p.ote_zone)
    nq_1m, es_1m = [], []
    for i in range(9):
        px = 20050 - i * 2
        nq_1m.append(c1(i, px, px + 2, px - 2, px - 1))
        es_1m.append(c1(i, 5600, 5601, 5599, 5600))
    # candle that tags the OTE and makes a lower low on NQ only
    tag = (zone_lo + zone_hi) / 2
    nq_1m.append(c1(9, tag + 3, tag + 4, tag - 1, tag))
    es_1m.append(c1(9, 5600, 5601, 5599.5, 5600))

    ready = eng.on_entry_candle(nq_1m, es_1m, nq_1m[-1].ts)
    assert ready is not None, "OTE tag + SMT should stage the setup"
    assert ready.state is SetupState.READY
    assert "SMT" in ready.smt_note
    assert ready.contracts >= 1


def test_backtest_contract_roll():
    from datetime import date
    from bot.backtest import contract_for

    assert contract_for("NQ", date(2025, 1, 10)) == "CON.F.US.ENQ.H25"
    assert contract_for("NQ", date(2025, 3, 10)) == "CON.F.US.ENQ.H25"
    assert contract_for("NQ", date(2025, 3, 20)) == "CON.F.US.ENQ.M25"  # rolled
    assert contract_for("ES", date(2025, 7, 1)) == "CON.F.US.EP.U25"
    assert contract_for("ES", date(2025, 12, 20)) == "CON.F.US.EP.H26"  # year wrap


def test_resolve_target_fixed_band():
    s = Settings()  # fixed mode, 30-50 pts
    # level inside the band -> target sits on the liquidity
    assert resolve_target(20000.0, 20042.0, Direction.LONG, s) == 20042.0
    # level beyond the cap -> clamped to 50
    assert resolve_target(20000.0, 20092.0, Direction.LONG, s) == 20050.0
    # level too close -> floored at 30
    assert resolve_target(20000.0, 20012.0, Direction.LONG, s) == 20030.0
    # no liquidity level -> band midpoint (40)
    assert resolve_target(20000.0, None, Direction.LONG, s) == 20040.0
    # shorts mirror
    assert resolve_target(20000.0, 19908.0, Direction.SHORT, s) == 19950.0
    assert resolve_target(20000.0, 19965.0, Direction.SHORT, s) == 19965.0


def test_resolve_target_internal_mode():
    s = Settings()
    s.target_mode = "internal"
    assert resolve_target(20000.0, 20092.0, Direction.LONG, s) == 20092.0
    assert resolve_target(20000.0, None, Direction.LONG, s) is None


def test_engine_internal_mode_still_targets_liquidity():
    s = Settings()
    s.smt_window = 10
    s.target_mode = "internal"
    eng = StrategyEngine(s)
    eng.on_structure_candle(build_5m())
    assert eng.pending is not None
    assert eng.pending.target == 20090.0  # the unswept internal high


def test_engine_invalidates_on_leg_violation():
    s = Settings()
    s.smt_window = 10
    eng = StrategyEngine(s)
    eng.on_structure_candle(build_5m())
    assert eng.pending is not None

    nq_1m = [c1(0, 19980, 19985, 19950, 19960)]  # closes through leg origin
    es_1m = [c1(0, 5600, 5601, 5599, 5600)]
    out = eng.on_entry_candle(nq_1m, es_1m, nq_1m[-1].ts)
    assert out is None and eng.pending is None
