"""Performance analytics computed from journal records.

All percentages that reference the account are against the Topstep account
size (default 50K), e.g. monthly return % = month P&L / account size.
"""

from __future__ import annotations

from collections import OrderedDict


def summary(recs: list[dict], account: float = 50_000.0) -> dict:
    n = len(recs)
    if n == 0:
        return {
            "trades": 0, "pnl": 0.0, "win_rate": None, "profit_factor": None,
            "expectancy": None, "avg_r": None, "avg_win": None, "avg_loss": None,
            "max_drawdown": 0.0, "best": None, "worst": None, "streak": 0,
            "return_pct": 0.0,
        }
    pnls = [r["pnl"] for r in recs]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    rs = [r["pnl"] / r["risk"] for r in recs if r.get("risk")]

    # max drawdown on the cumulative equity path
    cum = peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    # current win/loss streak (positive = wins, negative = losses)
    streak = 0
    for p in reversed(pnls):
        if p == 0:
            break
        if streak == 0:
            streak = 1 if p > 0 else -1
        elif (p > 0) == (streak > 0):
            streak += 1 if p > 0 else -1
        else:
            break

    return {
        "trades": n,
        "pnl": round(sum(pnls), 2),
        "win_rate": round(100 * len(wins) / n, 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(sum(pnls) / n, 2),
        "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        "max_drawdown": round(max_dd, 2),
        "best": round(max(pnls), 2),
        "worst": round(min(pnls), 2),
        "streak": streak,
        "return_pct": round(100 * sum(pnls) / account, 2),
    }


def monthly(recs: list[dict], account: float = 50_000.0) -> list[dict]:
    """Per-month rollup, oldest first: trades, win rate, P&L, return %."""
    buckets: OrderedDict[str, list[dict]] = OrderedDict()
    for r in sorted(recs, key=lambda r: r["closed"]):
        buckets.setdefault(r["month"], []).append(r)
    out = []
    for month, rows in buckets.items():
        pnls = [r["pnl"] for r in rows]
        wins = sum(1 for p in pnls if p > 0)
        out.append({
            "month": month,
            "trades": len(rows),
            "win_rate": round(100 * wins / len(rows), 1),
            "pnl": round(sum(pnls), 2),
            "return_pct": round(100 * sum(pnls) / account, 2),
        })
    return out


def equity_curve(recs: list[dict]) -> list[dict]:
    """Cumulative P&L after each trade, oldest first."""
    out = []
    cum = 0.0
    for r in sorted(recs, key=lambda r: r["closed"]):
        cum += r["pnl"]
        out.append({"t": r["date"], "cum": round(cum, 2), "pnl": r["pnl"]})
    return out


def recent(recs: list[dict], n: int = 25) -> list[dict]:
    """Latest n trades, newest first, trimmed for the history table."""
    rows = sorted(recs, key=lambda r: r["closed"], reverse=True)[:n]
    return [
        {
            "closed": r["closed"][:16].replace("T", " "),
            "direction": r["direction"],
            "symbol": r["symbol"],
            "contracts": r["contracts"],
            "entry": r["entry"],
            "exit": r["exit"],
            "pnl": r["pnl"],
            "r": round(r["pnl"] / r["risk"], 2) if r.get("risk") else None,
            "reason": r["reason"],
        }
        for r in rows
    ]
