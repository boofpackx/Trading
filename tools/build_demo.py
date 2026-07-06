"""Build the static GitHub Pages demo into docs/.

Replays a seeded sim session through the real bot loop, auto-confirming the
staged setup a few frames after it appears (so viewers see the setup card),
and records the UI state each simulated minute. The demo journal is also
pre-populated with trades mined from dozens of other sim sessions, backdated
across recent weekdays, so the analytics view has an equity curve and
monthly table to show. Output:

    docs/index.html    - ui/index.html with the demo-data script injected
    docs/demo-data.js  - window.DEMO_FRAMES = [...recorded states]

Usage:  python tools/build_demo.py [seed]
"""

from __future__ import annotations

import copy
import json
import os
import sys
from datetime import time as dtime, timedelta
from pathlib import Path

os.environ["BOT_JOURNAL"] = "off"  # demo journal is in-memory only
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data.sim import SimFeed  # noqa: E402
from bot.server import BotRunner  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIRM_DELAY = 3  # frames the staged card stays on screen before auto-confirm
HISTORY_SEEDS = 60  # sim sessions to mine for the demo's analytics history


def run_session(seed: int) -> BotRunner:
    """Run one full auto-confirmed sim session and return the runner."""
    r = BotRunner()
    r.sim = SimFeed(seed=seed)
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
    return r


def mine_history() -> list:
    """Real trades from many sim sessions, backdated over past weekdays."""
    trades = []
    for seed in range(100, 100 + HISTORY_SEEDS):
        trades.extend(run_session(seed).exec.stats.trades)
    day = 1
    for tr in reversed(trades):
        when = tr.closed - timedelta(days=day)
        while when.weekday() >= 5:
            when -= timedelta(days=1)
        tr.closed = when
        day += 2
    trades.sort(key=lambda t: t.closed)
    print(f"history: {len(trades)} trades mined from {HISTORY_SEEDS} sim sessions")
    return trades


def record(seed: int, history: list) -> list[dict] | None:
    r = BotRunner()
    r.sim = SimFeed(seed=seed)
    for tr in history:
        r.journal.append(tr, "sim")
    while r.sim.clock.time() < dtime(9, 26):
        nq, _ = r.sim.next_minute()
        r.nq_1m, r.es_1m = r.sim.nq_1m, r.sim.es_1m
        r.clock = nq.ts
        r._on_minute(nq)

    frames: list[dict] = []
    staged_for = 0
    trade_done_at = None
    for minute in range(400):
        nq, _ = r.sim.next_minute()
        r.nq_1m, r.es_1m = r.sim.nq_1m, r.sim.es_1m
        r.clock = nq.ts
        r._on_minute(nq)
        frames.append(copy.deepcopy(r.state()))

        if r.exec.staged:
            staged_for += 1
            if staged_for > CONFIRM_DELAY:
                r.exec.confirm(r.clock)
                r.engine.clear()
                staged_for = 0
        if r.exec.stats.trades and trade_done_at is None:
            trade_done_at = minute
        if trade_done_at is not None and minute - trade_done_at >= 12:
            break

    if not r.exec.stats.trades:
        return None
    t = r.exec.stats.trades[0]
    print(f"seed {seed}: recorded {len(frames)} frames, "
          f"trade {t.reason} {t.pnl:+.0f} @ {t.closed:%H:%M}")
    return frames


def main() -> None:
    seeds = [int(sys.argv[1])] if len(sys.argv) > 1 else list(range(20))
    history = mine_history()
    frames = None
    for seed in seeds:
        frames = record(seed, history)
        if frames:
            break
    if not frames:
        raise SystemExit("no seed produced a completed trade; widen the search")

    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "demo-data.js").write_text(
        "window.DEMO_FRAMES = " + json.dumps(frames, separators=(",", ":")) + ";\n"
    )

    html = (ROOT / "ui" / "index.html").read_text()
    html = html.replace(
        "<script>\nconst $ =",
        '<script src="demo-data.js"></script>\n<script>\nconst $ =',
    )
    assert 'demo-data.js' in html, "injection anchor not found in ui/index.html"
    (docs / "index.html").write_text(html)
    size_kb = (docs / "demo-data.js").stat().st_size // 1024
    print(f"wrote docs/index.html + docs/demo-data.js ({size_kb} KB)")


if __name__ == "__main__":
    main()
