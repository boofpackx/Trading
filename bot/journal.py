"""Persistent trade journal.

Every closed trade is appended to a JSONL file (one per mode, so paper
trades never contaminate live stats): journal.sim.jsonl / journal.live.jsonl
in the repo root. Set BOT_JOURNAL=off to disable persistence (in-memory only,
used by the demo builder), or JOURNAL_PATH to relocate the file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import TradeResult

ROOT = Path(__file__).resolve().parent.parent


def default_path(mode: str) -> Optional[Path]:
    if os.getenv("BOT_JOURNAL", "").lower() == "off":
        return None
    custom = os.getenv("JOURNAL_PATH")
    if custom:
        return Path(custom)
    return ROOT / f"journal.{mode}.jsonl"


class Journal:
    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.records: list[dict] = []
        if path is not None and path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        self.records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # skip a torn line rather than refuse to start

    def append(self, tr: TradeResult, mode: str) -> dict:
        rec = {
            "closed": tr.closed.isoformat(),
            "date": tr.closed.date().isoformat(),
            "month": tr.closed.strftime("%Y-%m"),
            "direction": tr.direction.value,
            "symbol": tr.symbol,
            "contracts": tr.contracts,
            "entry": tr.entry,
            "exit": tr.exit,
            "pnl": tr.pnl,
            "risk": tr.risk,
            "reason": tr.reason,
            "mode": mode,
        }
        self.records.append(rec)
        if self.path is not None:
            with self.path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    def for_day(self, day: datetime) -> list[dict]:
        key = day.date().isoformat()
        return [r for r in self.records if r.get("date") == key]

    def to_csv(self) -> str:
        cols = [
            "closed", "direction", "symbol", "contracts",
            "entry", "exit", "pnl", "risk", "reason", "mode",
        ]
        lines = [",".join(cols)]
        for r in self.records:
            lines.append(",".join(str(r.get(c, "")) for c in cols))
        return "\n".join(lines) + "\n"
