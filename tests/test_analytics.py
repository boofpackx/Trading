"""Analytics and journal tests."""

from datetime import datetime, timedelta
from pathlib import Path

from bot import analytics
from bot.config import ET, Settings
from bot.execution import ExecutionManager
from bot.journal import Journal
from bot.models import Direction, TradeResult

T0 = datetime(2026, 6, 1, 10, 0, tzinfo=ET)


def tr(i, pnl, risk=250.0, days=0):
    return TradeResult(
        setup_id=i, direction=Direction.LONG, symbol="NQ", contracts=1,
        entry=20000, exit=20000 + pnl / 20, pnl=pnl, reason="target" if pnl > 0 else "stop",
        closed=T0 + timedelta(days=days), risk=risk,
    )


def make_journal(specs):
    j = Journal(None)  # in-memory
    for i, (pnl, days) in enumerate(specs):
        j.append(tr(i, pnl, days=days), "sim")
    return j


def test_summary_math():
    j = make_journal([(500, 0), (-250, 1), (250, 2), (-250, 3)])
    s = analytics.summary(j.records, account=50_000)
    assert s["trades"] == 4
    assert s["pnl"] == 250.0
    assert s["win_rate"] == 50.0
    assert s["profit_factor"] == 1.5           # 750 / 500
    assert s["expectancy"] == 62.5
    assert s["avg_win"] == 375.0
    assert s["avg_loss"] == -250.0
    assert s["avg_r"] == 0.25                  # mean of (2, -1, 1, -1)
    assert s["max_drawdown"] == 250.0
    assert s["return_pct"] == 0.5              # 250 / 50k
    assert s["streak"] == -1


def test_summary_empty_and_no_losses():
    assert analytics.summary([])["trades"] == 0
    j = make_journal([(100, 0), (200, 1)])
    s = analytics.summary(j.records)
    assert s["profit_factor"] is None  # no losses -> undefined, not a crash
    assert s["streak"] == 2


def test_monthly_rollup():
    j = Journal(None)
    j.append(tr(1, 500, days=0), "sim")                      # June
    j.append(tr(2, -250, days=1), "sim")                     # June
    j.append(tr(3, 1000, days=35), "sim")                    # July
    months = analytics.monthly(j.records, account=50_000)
    assert [m["month"] for m in months] == ["2026-06", "2026-07"]
    assert months[0]["pnl"] == 250.0 and months[0]["return_pct"] == 0.5
    assert months[1]["pnl"] == 1000.0 and months[1]["return_pct"] == 2.0
    assert months[1]["win_rate"] == 100.0


def test_equity_curve_and_recent():
    j = make_journal([(500, 0), (-250, 1)])
    curve = analytics.equity_curve(j.records)
    assert [p["cum"] for p in curve] == [500.0, 250.0]
    rec = analytics.recent(j.records)
    assert rec[0]["pnl"] == -250 and rec[0]["r"] == -1.0  # newest first


def test_journal_persistence_roundtrip(tmp_path: Path):
    path = tmp_path / "journal.test.jsonl"
    j1 = Journal(path)
    j1.append(tr(1, 500), "sim")
    j1.append(tr(2, -250), "sim")
    j2 = Journal(path)
    assert len(j2.records) == 2
    assert j2.records[0]["pnl"] == 500
    csv = j2.to_csv()
    assert csv.splitlines()[0].startswith("closed,")
    assert len(csv.strip().splitlines()) == 3


def test_day_stats_rebuild_from_journal(tmp_path: Path):
    """A live-mode restart must remember today's losses (Topstep safety)."""
    path = tmp_path / "journal.live.jsonl"
    j = Journal(path)
    today = datetime.now(ET).replace(hour=10)
    for i, pnl in enumerate([-250, -250]):
        j.append(
            TradeResult(i, Direction.LONG, "NQ", 1, 20000, 19987.5, pnl,
                        "stop", today, risk=250.0),
            "live",
        )
    ex = ExecutionManager(Settings(), journal=Journal(path), rebuild_day=today)
    assert ex.stats.losses == 2
    assert ex.stats.halted
    ok, why = ex.guardrails.can_enter(ex.stats, today)
    assert not ok and "losses" in why
