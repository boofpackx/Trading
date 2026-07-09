"""Mystery Gift distribution backend.

Gen 7 Mystery Gift had three delivery paths: internet ("Get via Internet",
serving currently-live distributions), serial/passcode redemption, and
local wireless. The wondercard payload itself is the community-documented
WC7 format; essentially every event ever distributed is archived (e.g. in
Project Pokémon's event gallery), so a revival server's job is:

  1. hold a catalog of wondercard blobs with availability metadata
  2. serve the currently-live ones for "via Internet"
  3. redeem serial codes against code-gated cards, tracking one-time use

This module implements exactly that catalog + redemption logic. It treats
wondercards as opaque blobs on purpose: parsing WC7 internals server-side
is unnecessary for distribution, and blind reimplementation of the full
card layout without verification against PKHeX would invite silent
corruption. (A parse layer can be added once cards can be round-tripped
against reference tooling.)
"""

from __future__ import annotations

import sqlite3
import time

WC7_FULL_SIZE = 0x310  # size of a full wondercard file as distributed


class MysteryGiftError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_SCHEMA = """
CREATE TABLE IF NOT EXISTS gifts (
    gift_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    card         BLOB NOT NULL,
    -- delivery windows; NULL = unbounded
    live_from    REAL,
    live_until   REAL,
    -- 1 = shows up in "Get via Internet", 0 = serial-code only
    via_internet INTEGER NOT NULL DEFAULT 1,
    added_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS serial_codes (
    code       TEXT PRIMARY KEY,
    gift_id    INTEGER NOT NULL REFERENCES gifts(gift_id),
    max_uses   INTEGER NOT NULL DEFAULT 1,
    uses       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS redemptions (
    gift_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    redeemed_at REAL NOT NULL,
    PRIMARY KEY (gift_id, user_id)
);
"""


class MysteryGift:
    def __init__(self, db_path: str = ":memory:"):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)

    # -- catalog management (operator side) ------------------------------

    def add_gift(
        self,
        title: str,
        card: bytes,
        *,
        via_internet: bool = True,
        live_from: float | None = None,
        live_until: float | None = None,
    ) -> int:
        if not card:
            raise MysteryGiftError("bad_card", "empty wondercard")
        cur = self.db.execute(
            """INSERT INTO gifts (title, card, live_from, live_until, via_internet, added_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, card, live_from, live_until, int(via_internet), time.time()),
        )
        self.db.commit()
        return cur.lastrowid

    def add_serial_code(self, code: str, gift_id: int, *, max_uses: int = 1) -> None:
        code = code.strip().upper()
        if not code:
            raise MysteryGiftError("bad_code", "empty code")
        if self._gift(gift_id) is None:
            raise MysteryGiftError("not_found", f"no gift {gift_id}")
        self.db.execute(
            "INSERT OR REPLACE INTO serial_codes (code, gift_id, max_uses, uses) VALUES (?, ?, ?, 0)",
            (code, gift_id, max_uses),
        )
        self.db.commit()

    # -- player-facing ---------------------------------------------------

    def list_live(self, now: float | None = None) -> list[dict]:
        """Gifts currently offered via 'Get via Internet'."""
        now = time.time() if now is None else now
        rows = self.db.execute(
            """SELECT gift_id, title FROM gifts
               WHERE via_internet = 1
                 AND (live_from IS NULL OR live_from <= ?)
                 AND (live_until IS NULL OR live_until >= ?)
               ORDER BY added_at DESC""",
            (now, now),
        ).fetchall()
        return [dict(r) for r in rows]

    def download(self, gift_id: int, user_id: int, now: float | None = None) -> bytes:
        """Fetch a live internet gift. The real service allowed one card of a
        given distribution per save; we key that on user_id."""
        now = time.time() if now is None else now
        gift = self._gift(gift_id)
        if gift is None or not gift["via_internet"]:
            raise MysteryGiftError("not_found", f"no internet gift {gift_id}")
        if gift["live_from"] is not None and now < gift["live_from"]:
            raise MysteryGiftError("not_live", "distribution has not started")
        if gift["live_until"] is not None and now > gift["live_until"]:
            raise MysteryGiftError("not_live", "distribution has ended")
        self._record_redemption(gift_id, user_id)
        return gift["card"]

    def redeem_code(self, code: str, user_id: int) -> bytes:
        """Serial-code path. Codes have bounded uses; a user can redeem a
        given distribution once."""
        code = code.strip().upper()
        row = self.db.execute(
            "SELECT * FROM serial_codes WHERE code = ?", (code,)
        ).fetchone()
        if row is None:
            raise MysteryGiftError("bad_code", "unknown serial code")
        if row["uses"] >= row["max_uses"]:
            raise MysteryGiftError("used", "serial code already used")
        gift = self._gift(row["gift_id"])
        if gift is None:
            raise MysteryGiftError("not_found", "code points at a missing gift")
        self._record_redemption(gift["gift_id"], user_id)
        self.db.execute(
            "UPDATE serial_codes SET uses = uses + 1 WHERE code = ?", (code,)
        )
        self.db.commit()
        return gift["card"]

    # -- internals ---------------------------------------------------------

    def _gift(self, gift_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM gifts WHERE gift_id = ?", (gift_id,)
        ).fetchone()

    def _record_redemption(self, gift_id: int, user_id: int) -> None:
        try:
            self.db.execute(
                "INSERT INTO redemptions (gift_id, user_id, redeemed_at) VALUES (?, ?, ?)",
                (gift_id, user_id, time.time()),
            )
        except sqlite3.IntegrityError:
            raise MysteryGiftError("used", "already redeemed this distribution")
        self.db.commit()
