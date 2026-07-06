"""Order lifecycle: staging, one-click confirmation, fills, and the
flat-before-close rule.

In sim mode fills are modeled against incoming 1m candles (entry limit fills
when price trades through it; stop/target fill on the candle that tags them,
stop wins ties conservatively). In live mode a bracket order goes to
ProjectX on confirm and the same candle-based tracker mirrors state for the
UI while the exchange-side brackets do the real work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from .config import SETTINGS, Settings
from .models import (
    Candle,
    DayStats,
    Direction,
    Position,
    Setup,
    SetupState,
    TradeResult,
)
from .risk import Guardrails


class ExecutionManager:
    def __init__(
        self,
        settings: Settings = SETTINGS,
        log: Optional[Callable[[str], None]] = None,
        broker=None,  # ProjectXClient in live mode, None in sim
        journal=None,  # Journal instance; None disables persistence
        rebuild_day: Optional[datetime] = None,  # restore guardrails from journal
    ):
        self.s = settings
        self.log = log or (lambda _msg: None)
        self.broker = broker
        self.journal = journal
        self.guardrails = Guardrails(settings)
        self.stats = DayStats()
        self.staged: Optional[Setup] = None
        self.working: Optional[Setup] = None  # confirmed, waiting for limit fill
        self.position: Optional[Position] = None
        if journal is not None and rebuild_day is not None:
            self._rebuild_day_stats(rebuild_day)

    def _rebuild_day_stats(self, day: datetime) -> None:
        """Restore today's guardrail state from the journal so a mid-session
        restart cannot forget losses already taken (Topstep safety)."""
        for rec in self.journal.for_day(day):
            self.stats.pnl += rec["pnl"]
            if rec["pnl"] < 0:
                self.stats.losses += 1
            elif rec["pnl"] > 0:
                self.stats.wins += 1
        self.guardrails.refresh_halt(self.stats)
        if self.stats.pnl or self.stats.losses or self.stats.wins:
            self.log(
                f"restored today's stats from journal: {self.stats.pnl:+.2f}, "
                f"{self.stats.wins}W-{self.stats.losses}L"
                + (f" — {self.stats.halt_reason}" if self.stats.halted else "")
            )

    # ------------------------------------------------------------- staging
    def stage(self, setup: Setup, now_et: datetime) -> bool:
        ok, why = self.guardrails.can_enter(self.stats, now_et)
        if not ok:
            self.log(f"setup #{setup.id} suppressed: {why}")
            setup.state = SetupState.EXPIRED
            return False
        if self.position or self.working:
            self.log(f"setup #{setup.id} suppressed: already in a trade")
            setup.state = SetupState.EXPIRED
            return False
        self.staged = setup
        return True

    def confirm(self, now_et: datetime) -> tuple[bool, str]:
        if not self.staged:
            return False, "nothing staged"
        ok, why = self.guardrails.can_enter(self.stats, now_et)
        if not ok:
            self.staged.state = SetupState.EXPIRED
            self.staged = None
            return False, why
        setup = self.staged
        if self.broker is not None:
            try:
                order_id = self.broker.place_bracket(
                    symbol=setup.symbol,
                    side_buy=setup.direction is Direction.LONG,
                    size=setup.contracts,
                    limit_price=setup.entry,
                    stop_price=setup.stop,
                    target_price=setup.target,
                )
                self.log(f"bracket order {order_id} placed on {setup.symbol}")
            except Exception as e:  # surface broker failures, keep bot alive
                self.log(f"ORDER REJECTED: {e}")
                return False, str(e)
        setup.state = SetupState.CONFIRMED
        self.working = setup
        self.staged = None
        self.log(
            f"confirmed #{setup.id}: {setup.direction.value} {setup.contracts} "
            f"{setup.symbol} @ {setup.entry:.2f}, stop {setup.stop:.2f}, "
            f"target {setup.target:.2f}"
        )
        return True, ""

    def skip(self) -> bool:
        if not self.staged:
            return False
        self.staged.state = SetupState.SKIPPED
        self.log(f"setup #{self.staged.id} skipped")
        self.staged = None
        return True

    # --------------------------------------------------------------- fills
    def on_candle(self, c: Candle, now_et: datetime) -> None:
        """Advance fill/exit state on each 1m close of the traded symbol."""
        # an unfilled entry does not outlive the AM entry window
        if self.working and not self.guardrails.in_entry_window(now_et):
            self.cancel_working("entry window closed")

        if self.working:
            w = self.working
            hit = c.low <= w.entry if w.direction is Direction.LONG else c.high >= w.entry
            through_stop = (
                c.low <= w.stop if w.direction is Direction.LONG else c.high >= w.stop
            )
            if hit:
                w.state = SetupState.FILLED
                from .config import MNQ, NQ

                pv = NQ.point_value if w.symbol == "NQ" else MNQ.point_value
                self.position = Position(
                    setup_id=w.id,
                    direction=w.direction,
                    symbol=w.symbol,
                    contracts=w.contracts,
                    entry=w.entry,
                    stop=w.stop,
                    target=w.target,
                    point_value=pv,
                    opened=c.ts,
                )
                self.working = None
                self.log(f"FILLED #{w.id} @ {w.entry:.2f}")
                if through_stop:  # same candle ran to the stop — resolve it
                    self._close(self.position.stop, "stop", now_et)
                return

        if self.position:
            p = self.position
            if p.direction is Direction.LONG:
                if c.low <= p.stop:
                    self._close(p.stop, "stop", now_et)
                elif c.high >= p.target:
                    self._close(p.target, "target", now_et)
                else:
                    p.unrealized = (c.close - p.entry) * p.point_value * p.contracts
            else:
                if c.high >= p.stop:
                    self._close(p.stop, "stop", now_et)
                elif c.low <= p.target:
                    self._close(p.target, "target", now_et)
                else:
                    p.unrealized = (p.entry - c.close) * p.point_value * p.contracts

        # flat-before-close rule
        if self.guardrails.must_be_flat(now_et):
            self.cancel_working("flat-by window")
            if self.position:
                self._close(c.close, "flat_close", now_et)

    def cancel_working(self, why: str) -> None:
        if self.working and self.broker is not None:
            try:  # pull the resting bracket order broker-side too
                self.broker.flatten()
            except Exception as e:
                self.log(f"broker cancel failed: {e}")
        if self.working:
            self.log(f"unfilled order #{self.working.id} cancelled ({why})")
            self.working.state = SetupState.EXPIRED
            self.working = None
        if self.staged:
            self.staged.state = SetupState.EXPIRED
            self.staged = None

    def flatten_now(self, last_close: float, now_et: datetime) -> None:
        if self.broker is not None:
            try:
                self.broker.flatten()
            except Exception as e:
                self.log(f"flatten error: {e}")
        self.cancel_working("manual flatten")
        if self.position:
            self._close(last_close, "manual", now_et)

    def _close(self, price: float, reason: str, now_et: datetime) -> None:
        p = self.position
        if not p:
            return
        sign = 1 if p.direction is Direction.LONG else -1
        pnl = (price - p.entry) * sign * p.point_value * p.contracts
        result = TradeResult(
            setup_id=p.setup_id,
            direction=p.direction,
            symbol=p.symbol,
            contracts=p.contracts,
            entry=p.entry,
            exit=price,
            pnl=round(pnl, 2),
            reason=reason,
            closed=now_et,
            risk=round(abs(p.entry - p.stop) * p.point_value * p.contracts, 2),
        )
        self.guardrails.record(self.stats, result)
        if self.journal is not None:
            self.journal.append(result, self.s.mode)
        self.position = None
        self.log(
            f"CLOSED #{result.setup_id} {reason} @ {price:.2f} for "
            f"{'+' if pnl >= 0 else ''}{pnl:.2f} (day {self.stats.pnl:+.2f})"
        )
        if self.stats.halted:
            self.log(f"GUARDRAIL: {self.stats.halt_reason}")
