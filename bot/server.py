"""FastAPI server: runs the bot loop and serves the e-ink UI.

    python -m bot            # sim mode by default
    BOT_MODE=live python -m bot   # trade the TopstepX account from .env
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse

from . import analytics
from .config import ET, SETTINGS
from .data.aggregator import closed_only
from .data.sim import SimFeed
from .execution import ExecutionManager
from .journal import Journal, default_path
from .models import Candle, SetupState
from .strategy import fib
from .strategy.engine import StrategyEngine

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


class BotRunner:
    def __init__(self):
        self.s = SETTINGS
        self.events: deque[dict] = deque(maxlen=80)
        self.engine = StrategyEngine(self.s, log=self._log)
        self.broker = None
        if self.s.mode == "live":
            from .data.projectx import ProjectXClient

            self.broker = ProjectXClient(self.s)
        self.journal = Journal(default_path(self.s.mode))
        # live guardrails survive restarts; sim starts each run fresh
        rebuild = datetime.now(ET) if self.s.mode == "live" else None
        self.exec = ExecutionManager(
            self.s, log=self._log, broker=self.broker,
            journal=self.journal, rebuild_day=rebuild,
        )
        self.nq_1m: list[Candle] = []
        self.es_1m: list[Candle] = []
        self.sim = SimFeed() if self.s.mode == "sim" else None
        self.clock: datetime = datetime.now(ET)
        self.sockets: set[WebSocket] = set()
        self.speed = 1.0  # sim replay speed multiplier; 0 pauses
        self._last_5m_count = 0

    def _log(self, msg: str) -> None:
        self.events.appendleft({"ts": self.clock.strftime("%H:%M:%S"), "msg": msg})

    # ------------------------------------------------------------- the loop
    async def run(self):
        self._log(f"bot started in {self.s.mode.upper()} mode")
        if self.sim is not None:
            # replay pre-market instantly so structure exists at the open
            from datetime import time as dtime

            while self.sim.clock.time() < dtime(9, 28):
                nq, _ = self.sim.next_minute()
                self.nq_1m, self.es_1m = self.sim.nq_1m, self.sim.es_1m
                self.clock = nq.ts
                self._on_minute(nq)
        if self.broker is not None:
            try:
                self.broker.login()
                self.broker.resolve_account()
                self._log(f"ProjectX account {self.broker.account_id} connected")
            except Exception as e:
                self._log(f"ProjectX login failed: {e} — falling back to SIM")
                self.broker = None
                self.exec.broker = None
                self.sim = SimFeed()
                # relabel + re-point the journal so paper trades never land
                # in the live history
                self.s.mode = "sim"
                self.journal = Journal(default_path("sim"))
                self.exec.journal = self.journal
        while True:
            try:
                if self.sim is not None:
                    if self.speed <= 0:
                        await asyncio.sleep(0.3)  # paused
                        continue
                    nq, es = self.sim.next_minute()
                    self.nq_1m, self.es_1m = self.sim.nq_1m, self.sim.es_1m
                    self.clock = nq.ts
                    self._on_minute(nq)
                    await self._broadcast()
                    await asyncio.sleep(0.8 / self.speed)  # 1 sim-minute per tick
                else:
                    nq_bars = self.broker.recent_1m_bars("NQ")
                    es_bars = self.broker.recent_1m_bars("ES")
                    if nq_bars and (not self.nq_1m
                                    or nq_bars[-1].ts > self.nq_1m[-1].ts):
                        self.nq_1m, self.es_1m = nq_bars, es_bars
                        self.clock = datetime.now(ET)
                        self._on_minute(nq_bars[-1])
                        await self._broadcast()
                    await asyncio.sleep(5)
            except Exception as e:
                self._log(f"loop error: {e}")
                await asyncio.sleep(5)

    def _on_minute(self, last_nq: Candle) -> None:
        now = self.clock
        # 5m structure update whenever a new 5m candle completes
        nq_5m = closed_only(self.nq_1m, self.s.structure_tf)
        if len(nq_5m) != self._last_5m_count:
            self._last_5m_count = len(nq_5m)
            if not self.exec.position and not self.exec.working and not self.exec.staged:
                self.engine.on_structure_candle(nq_5m)

        # invalidate a staged setup if the leg origin is violated
        st = self.exec.staged
        if st and not fib.leg_valid(st.direction, last_nq.close, st.leg_start):
            st.state = SetupState.INVALIDATED
            self._log(f"staged setup #{st.id} invalidated: leg origin violated")
            self.exec.staged = None
            self.engine.clear()

        # 1m entry logic
        ready = self.engine.on_entry_candle(self.nq_1m, self.es_1m, now)
        if ready is not None:
            if not self.exec.stage(ready, now):
                self.engine.clear()

        # fills / exits / flat-by rule
        self.exec.on_candle(last_nq, now)
        if self.exec.position is None and self.exec.working is None:
            # after a completed trade the engine may hold a stale READY setup
            if self.engine.pending and self.engine.pending.state in (
                SetupState.CONFIRMED,
                SetupState.FILLED,
                SetupState.EXPIRED,
            ):
                self.engine.clear()

    # ---------------------------------------------------------------- state
    def state(self) -> dict:
        g = self.exec.guardrails
        candles = self.nq_1m[-150:]
        staged = self.exec.staged
        # chart overlay: staged setup > working order > open position > pending leg
        if staged is not None:
            overlay = self._setup_dict(staged)
        elif self.exec.working is not None:
            overlay = self._setup_dict(self.exec.working)
        elif self.exec.position is not None:
            p = self.exec.position
            overlay = {
                "kind": "position",
                "direction": p.direction.value,
                "entry": p.entry,
                "stop": p.stop,
                "target": p.target,
                "ote_upper": p.entry,
                "ote_lower": p.entry,
            }
        elif (
            self.engine.pending is not None
            and self.engine.pending.state is SetupState.AWAITING_RETRACE
        ):
            overlay = self._setup_dict(self.engine.pending)
        else:
            overlay = None
        return {
            "mode": self.s.mode if self.sim is None else "sim",
            "clock": self.clock.strftime("%H:%M:%S"),
            "in_window": g.in_entry_window(self.clock),
            "flat_by": self.s.flat_by.strftime("%H:%M"),
            "candles": [
                {"t": c.ts.strftime("%H:%M"), "o": c.open, "h": c.high, "l": c.low, "c": c.close}
                for c in candles
            ],
            "setup": self._setup_dict(staged),
            "pending_leg": overlay if staged is None else None,
            "position": self._position_dict(),
            "stats": {
                "pnl": round(self.exec.stats.pnl, 2),
                "wins": self.exec.stats.wins,
                "losses": self.exec.stats.losses,
                "halted": self.exec.stats.halted,
                "halt_reason": self.exec.stats.halt_reason,
                "risk_per_trade": self.s.risk_per_trade,
                "max_losses": self.s.max_daily_losses,
                "max_drawdown": self.s.max_daily_drawdown,
            },
            "events": list(self.events),
            "speed": self.speed,
            "analytics": {
                "summary": analytics.summary(self.journal.records),
                "monthly": analytics.monthly(self.journal.records),
                "curve": analytics.equity_curve(self.journal.records),
                "recent": analytics.recent(self.journal.records),
            },
        }

    def _setup_dict(self, s) -> dict | None:
        if s is None:
            return None
        return {
            "kind": "setup",
            "id": s.id,
            "direction": s.direction.value,
            "state": s.state.value,
            "entry": s.entry,
            "stop": s.stop,
            "target": s.target,
            "leg_start": s.leg_start,
            "leg_end": s.leg_end,
            "ote_upper": max(s.ote_zone),
            "ote_lower": min(s.ote_zone),
            "rr": s.rr,
            "symbol": s.symbol,
            "contracts": s.contracts,
            "dollar_risk": s.dollar_risk,
            "smt": s.smt_note,
            "note": s.note,
        }

    def _position_dict(self) -> dict | None:
        p = self.exec.position
        if p is None:
            w = self.exec.working
            if w is None:
                return None
            return {
                "status": "working",
                "direction": w.direction.value,
                "symbol": w.symbol,
                "contracts": w.contracts,
                "entry": w.entry,
                "stop": w.stop,
                "target": w.target,
                "unrealized": 0.0,
            }
        return {
            "status": "open",
            "direction": p.direction.value,
            "symbol": p.symbol,
            "contracts": p.contracts,
            "entry": p.entry,
            "stop": p.stop,
            "target": p.target,
            "unrealized": round(p.unrealized, 2),
        }

    async def _broadcast(self):
        if not self.sockets:
            return
        payload = json.dumps(self.state())
        dead = set()
        for ws in self.sockets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self.sockets -= dead


runner = BotRunner()
app = FastAPI(title="ICT NY-AM Bot")


@app.on_event("startup")
async def _start():
    asyncio.create_task(runner.run())


@app.get("/")
async def index():
    return FileResponse(UI_DIR / "index.html")


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    runner.sockets.add(websocket)
    try:
        await websocket.send_text(json.dumps(runner.state()))
        while True:
            await websocket.receive_text()  # keepalive pings; state is pushed
    except WebSocketDisconnect:
        pass
    finally:
        runner.sockets.discard(websocket)


@app.post("/api/confirm")
async def confirm():
    ok, why = runner.exec.confirm(runner.clock)
    if ok:
        runner.engine.clear()
    await runner._broadcast()
    return {"ok": ok, "reason": why}


@app.post("/api/skip")
async def skip():
    ok = runner.exec.skip()
    runner.engine.clear()
    await runner._broadcast()
    return {"ok": ok}


@app.post("/api/flatten")
async def flatten():
    last = runner.nq_1m[-1].close if runner.nq_1m else 0.0
    runner.exec.flatten_now(last, runner.clock)
    runner.engine.clear()
    await runner._broadcast()
    return {"ok": True}


@app.post("/api/speed")
async def set_speed(x: float):
    if runner.sim is None:
        return {"ok": False, "reason": "speed control is sim-only"}
    runner.speed = max(0.0, min(x, 16.0))
    await runner._broadcast()
    return {"ok": True, "speed": runner.speed}


@app.get("/api/journal.csv")
async def journal_csv():
    return PlainTextResponse(
        runner.journal.to_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=journal.csv"},
    )
