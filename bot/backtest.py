"""Backtest the strategy over historical sessions.

Real-data mode (requires PROJECTX_USERNAME / PROJECTX_API_KEY in .env):

    python -m bot.backtest --start 2025-01-01 --end 2025-12-31

For each weekday it pulls real 1m NQ and ES bars from the ProjectX history
API (quarterly contracts resolved by date), replays the session through the
exact same engine/execution/guardrail stack with auto-confirm, and prints
the analytics report: monthly returns, win rate, profit factor, expectancy,
max drawdown.

Synthetic mode (no credentials; proves the harness, NOT the edge):

    python -m bot.backtest --synthetic 252

Notes on real data: expired-contract history availability varies by data
provider; if a day's bars come back empty it is skipped and counted. The
quarterly roll is approximated as mid-month of the expiry month (H/M/U/Z).
Override with --contract-nq / --contract-es to pin explicit contract ids.
"""

from __future__ import annotations

import argparse
import os
import time as systime
from datetime import date, datetime, time as dtime, timedelta

os.environ.setdefault("BOT_JOURNAL", "off")  # backtests never touch journals

from .config import ET, SETTINGS  # noqa: E402
from . import analytics  # noqa: E402
from .journal import Journal  # noqa: E402
from .models import Candle  # noqa: E402

QUARTERS = [(3, "H"), (6, "M"), (9, "U"), (12, "Z")]
ROOTS = {"NQ": "ENQ", "ES": "EP"}


def contract_for(symbol: str, d: date) -> str:
    """Quarterly contract id by date, rolling mid-month of the expiry month
    (approximation of the actual roll)."""
    root = ROOTS[symbol]
    yy = str(d.year)[2:]
    for qm, code in QUARTERS:
        if d.month < qm or (d.month == qm and d.day <= 15):
            return f"CON.F.US.{root}.{code}{yy}"
    return f"CON.F.US.{root}.H{str(d.year + 1)[2:]}"


def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def replay_day(nq_bars: list[Candle], es_bars: list[Candle]):
    """Drive one session's bars through the live bot stack, auto-confirming
    staged setups. Returns the day's TradeResults."""
    from .server import BotRunner

    r = BotRunner()
    r.sim = None
    j = 0
    for i, bar in enumerate(nq_bars):
        while j < len(es_bars) and es_bars[j].ts <= bar.ts:
            j += 1
        r.nq_1m = nq_bars[: i + 1]
        r.es_1m = es_bars[:j]
        r.clock = bar.ts
        r._on_minute(bar)
        if r.exec.staged:
            r.exec.confirm(r.clock)
            r.engine.clear()
    return r.exec.stats.trades


def run_synthetic(sessions: int) -> Journal:
    from .data.sim import SimFeed
    from .server import BotRunner

    journal = Journal(None)
    day = date(2025, 1, 1)
    done = 0
    while done < sessions:
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        r = BotRunner()
        r.sim = SimFeed(seed=done)
        while r.sim.clock.time() < dtime(9, 26):
            nq, _ = r.sim.next_minute()
            r.nq_1m, r.es_1m = r.sim.nq_1m, r.sim.es_1m
            r.clock = nq.ts
            r._on_minute(nq)
        for _ in range(400):
            nq, _ = r.sim.next_minute()
            r.nq_1m, r.es_1m = r.sim.nq_1m, r.sim.es_1m
            r.clock = nq.ts
            r._on_minute(nq)
            if r.exec.staged:
                r.exec.confirm(r.clock)
                r.engine.clear()
        for tr in r.exec.stats.trades:
            tr.closed = tr.closed.replace(year=day.year, month=day.month, day=day.day)
            journal.append(tr, "backtest")
        done += 1
        day += timedelta(days=1)
        if done % 25 == 0:
            print(f"  {done}/{sessions} sessions")
    return journal


def run_real(start: date, end: date, contract_nq: str | None,
             contract_es: str | None, delay: float) -> Journal:
    from .data.projectx import ProjectXClient

    client = ProjectXClient(SETTINGS)
    client.login()
    journal = Journal(None)
    skipped = 0
    days = list(weekdays(start, end))
    for n, d in enumerate(days, 1):
        cid_nq = contract_nq or contract_for("NQ", d)
        cid_es = contract_es or contract_for("ES", d)
        t0 = datetime(d.year, d.month, d.day, 8, 0, tzinfo=ET)
        t1 = datetime(d.year, d.month, d.day, 16, 10, tzinfo=ET)
        try:
            nq = client.bars_between(cid_nq, t0, t1)
            es = client.bars_between(cid_es, t0, t1)
        except Exception as e:
            print(f"  {d}: fetch failed ({e}) — skipped")
            skipped += 1
            continue
        if len(nq) < 120 or len(es) < 120:
            skipped += 1
            continue  # holiday / missing data
        for tr in replay_day(nq, es):
            journal.append(tr, "backtest")
        if n % 21 == 0:
            print(f"  {n}/{len(days)} days ({len(journal.records)} trades so far)")
        systime.sleep(delay)
    if skipped:
        print(f"  note: {skipped} day(s) skipped (no/short data)")
    return journal


def report(journal: Journal, account: float = 50_000.0) -> None:
    recs = journal.records
    s = analytics.summary(recs, account)
    print("\n================ BACKTEST REPORT ================")
    if not recs:
        print("no trades were taken over the tested span")
        return
    first, last = recs[0]["date"], recs[-1]["date"]
    print(f"span {first} -> {last}   trades {s['trades']}")
    print(
        f"net P&L ${s['pnl']:+,.0f}   return {s['return_pct']:+.2f}% on "
        f"${account:,.0f}"
    )
    print(
        f"win rate {s['win_rate']}%   profit factor "
        f"{s['profit_factor'] if s['profit_factor'] is not None else 'inf'}   "
        f"expectancy ${s['expectancy']:+,.0f}/trade"
        + (f" ({s['avg_r']:+.2f}R)" if s["avg_r"] is not None else "")
    )
    print(
        f"avg win ${s['avg_win'] or 0:,.0f}   avg loss ${s['avg_loss'] or 0:,.0f}   "
        f"max drawdown ${s['max_drawdown']:,.0f}"
    )
    print("\nmonth      trades   win%      P&L    return%")
    for m in analytics.monthly(recs, account):
        print(
            f"{m['month']}   {m['trades']:6d}   {m['win_rate']:5.1f}%  "
            f"${m['pnl']:+8,.0f}   {m['return_pct']:+6.2f}%"
        )
    print("=================================================")
    print("Reminder: past results (and especially synthetic results) do not")
    print("guarantee future performance.")


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--synthetic", type=int, metavar="N",
                    help="run N synthetic sessions instead of real data")
    ap.add_argument("--contract-nq", help="pin an explicit NQ contract id")
    ap.add_argument("--contract-es", help="pin an explicit ES contract id")
    ap.add_argument("--delay", type=float, default=0.25,
                    help="seconds between history API calls")
    args = ap.parse_args()

    SETTINGS.mode = "sim"  # keeps BotRunner from building a live broker

    if args.synthetic:
        print(f"synthetic backtest: {args.synthetic} generated sessions "
              f"(machinery check — NOT real market results)")
        journal = run_synthetic(args.synthetic)
    else:
        if not (SETTINGS.projectx_username and SETTINGS.projectx_api_key):
            raise SystemExit(
                "real-data backtest needs PROJECTX_USERNAME/PROJECTX_API_KEY "
                "in .env — or use --synthetic N for a machinery check"
            )
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        print(f"real-data backtest {start} -> {end}")
        journal = run_real(start, end, args.contract_nq, args.contract_es,
                           args.delay)
    report(journal)


if __name__ == "__main__":
    main()
