"""TopstepX / ProjectX Gateway API client.

Covers what the bot needs: session-key auth, contract lookup, historical 1m
bars (polled for live updates), and order placement with an attached stop and
target. Real-time SignalR streaming can be layered in later; polling
retrieveBars every few seconds is well within rate limits and keeps the
dependency footprint small.

API reference: https://gateway.docs.projectx.com
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from ..config import ET, Settings
from ..models import Candle


class ProjectXError(RuntimeError):
    pass


class ProjectXClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.base = settings.projectx_url.rstrip("/")
        self.token: Optional[str] = None
        self.account_id: Optional[int] = None
        self.http = httpx.Client(timeout=15.0)
        self.contract_ids: dict[str, str] = {}  # symbol -> contractId

    # ------------------------------------------------------------- session
    def login(self) -> None:
        r = self.http.post(
            f"{self.base}/api/Auth/loginKey",
            json={"userName": self.s.projectx_username, "apiKey": self.s.projectx_api_key},
        )
        data = self._ok(r)
        self.token = data["token"]

    def _headers(self) -> dict:
        if not self.token:
            self.login()
        return {"Authorization": f"Bearer {self.token}"}

    def _post(self, path: str, payload: dict) -> dict:
        r = self.http.post(f"{self.base}{path}", json=payload, headers=self._headers())
        if r.status_code == 401:  # token expired — re-auth once
            self.login()
            r = self.http.post(f"{self.base}{path}", json=payload, headers=self._headers())
        return self._ok(r)

    @staticmethod
    def _ok(r: httpx.Response) -> dict:
        r.raise_for_status()
        data = r.json()
        if data.get("success") is False:
            raise ProjectXError(f"gateway error: {data.get('errorMessage') or data}")
        return data

    # ------------------------------------------------------------- account
    def resolve_account(self) -> int:
        if self.s.projectx_account_id:
            self.account_id = int(self.s.projectx_account_id)
            return self.account_id
        data = self._post("/api/Account/search", {"onlyActiveAccounts": True})
        accounts = data.get("accounts") or []
        if not accounts:
            raise ProjectXError("no active accounts on this ProjectX login")
        self.account_id = accounts[0]["id"]
        return self.account_id

    def resolve_contract(self, symbol: str) -> str:
        """Front-month contract id for a root symbol (NQ, MNQ, ES)."""
        if symbol in self.contract_ids:
            return self.contract_ids[symbol]
        data = self._post("/api/Contract/search", {"searchText": symbol, "live": False})
        contracts = data.get("contracts") or []
        match = next(
            (c for c in contracts if c.get("name", "").startswith(symbol)), None
        ) or (contracts[0] if contracts else None)
        if not match:
            raise ProjectXError(f"no contract found for {symbol}")
        self.contract_ids[symbol] = match["id"]
        return match["id"]

    # ---------------------------------------------------------------- data
    def bars_between(
        self, contract_id: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """1m bars for an explicit contract id and UTC/aware time range."""
        data = self._post(
            "/api/History/retrieveBars",
            {
                "contractId": contract_id,
                "live": False,
                "startTime": start.isoformat(),
                "endTime": end.isoformat(),
                "unit": 2,  # minute
                "unitNumber": 1,
                "limit": 20000,
                "includePartialBar": False,
            },
        )
        bars = data.get("bars") or []
        out = [
            Candle(
                ts=datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET),
                open=b["o"],
                high=b["h"],
                low=b["l"],
                close=b["c"],
            )
            for b in bars
        ]
        out.sort(key=lambda c: c.ts)
        return out

    def recent_1m_bars(self, symbol: str, minutes: int = 240) -> list[Candle]:
        contract_id = self.resolve_contract(symbol)
        now = datetime.now(timezone.utc)
        return self.bars_between(contract_id, now - timedelta(minutes=minutes), now)

    # -------------------------------------------------------------- orders
    def place_bracket(
        self,
        symbol: str,
        side_buy: bool,
        size: int,
        limit_price: float,
        stop_price: float,
        target_price: float,
    ) -> int:
        """Limit entry with attached stop-loss and take-profit brackets."""
        if self.account_id is None:
            self.resolve_account()
        contract_id = self.resolve_contract(symbol)
        data = self._post(
            "/api/Order/place",
            {
                "accountId": self.account_id,
                "contractId": contract_id,
                "type": 1,  # limit
                "side": 0 if side_buy else 1,
                "size": size,
                "limitPrice": limit_price,
                "stopLossBracket": {"price": stop_price},
                "takeProfitBracket": {"price": target_price},
            },
        )
        return data.get("orderId", -1)

    def flatten(self) -> None:
        """Close all open positions and cancel working orders."""
        if self.account_id is None:
            self.resolve_account()
        pos = self._post("/api/Position/searchOpen", {"accountId": self.account_id})
        for p in pos.get("positions") or []:
            self._post(
                "/api/Position/closeContract",
                {"accountId": self.account_id, "contractId": p["contractId"]},
            )
        orders = self._post(
            "/api/Order/searchOpen", {"accountId": self.account_id}
        )
        for o in orders.get("orders") or []:
            self._post(
                "/api/Order/cancel", {"accountId": self.account_id, "orderId": o["id"]}
            )
