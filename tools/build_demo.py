"""Build the static GitHub Pages demo into docs/.

Replays a seeded sim session through the real bot loop, auto-confirming the
staged setup a few frames after it appears (so viewers see the setup card),
and records the UI state each simulated minute. Output:

    docs/index.html    - ui/index.html with the demo-data script injected
    docs/demo-data.js  - window.DEMO_FRAMES = [...recorded states]

Usage:  python tools/build_demo.py [seed]
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data.sim import SimFeed  # noqa: E402
from bot.server import BotRunner  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIRM_DELAY = 3  # frames the staged card stays on screen before auto-confirm


def record(seed: int) -> list[dict] | None:
    r = BotRunner()
    r.sim = SimFeed(seed=seed)
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
    frames = None
    for seed in seeds:
        frames = record(seed)
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
