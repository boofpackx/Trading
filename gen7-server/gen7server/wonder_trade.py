"""Wonder Trade backend.

In Gen 7 Wonder Trade lives in Festival Plaza: you offer a Pokémon, the
server pairs you with another random participant, and you each receive the
other's mon sight-unseen.

The matching model here is a simple FIFO pool with the same fairness
properties players expect:

  * you can never be matched with yourself (by user id)
  * a mon is exchanged at most once
  * entries can be cancelled while still unmatched
  * completed matches are held until each side collects its result

Like the GTS module, storage keeps the encrypted blob untouched and only
indexes decrypted metadata.
"""

from __future__ import annotations

import secrets
import sqlite3
import time

from . import legality
from .pk7 import PK7, SIZE_STORED


class WonderTradeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


STATE_WAITING = 0
STATE_MATCHED = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wt_entries (
    entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    password    INTEGER NOT NULL,
    owner_id    INTEGER NOT NULL,
    state       INTEGER NOT NULL DEFAULT 0,
    entered_at  REAL NOT NULL,
    pokemon     BLOB NOT NULL,
    result      BLOB,
    species     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wt_wait ON wt_entries (state, entered_at);
"""


class WonderTrade:
    def __init__(self, db_path: str = ":memory:", *, require_legal: bool = True):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.require_legal = require_legal

    def enter(self, owner_id: int, pokemon_encrypted: bytes) -> dict:
        """Join the pool. If a partner is already waiting, the match completes
        immediately and the partner's mon is returned; otherwise the entry
        waits and the caller polls with collect()."""
        if len(pokemon_encrypted) != SIZE_STORED:
            raise WonderTradeError("bad_size", f"pokemon must be {SIZE_STORED} bytes")
        pk = PK7.from_encrypted(pokemon_encrypted)
        if self.require_legal:
            problems = legality.check(pk)
            if problems:
                raise WonderTradeError("illegal", "; ".join(problems))
        if pk.is_egg:
            raise WonderTradeError("illegal", "eggs cannot be wonder traded")

        partner = self.db.execute(
            """SELECT * FROM wt_entries WHERE state = ? AND owner_id != ?
               ORDER BY entered_at ASC LIMIT 1""",
            (STATE_WAITING, owner_id),
        ).fetchone()

        if partner is not None:
            # Complete the match: partner's entry holds our mon as its result.
            self.db.execute(
                "UPDATE wt_entries SET state = ?, result = ? WHERE entry_id = ?",
                (STATE_MATCHED, pokemon_encrypted, partner["entry_id"]),
            )
            self.db.commit()
            return {"matched": True, "pokemon": partner["pokemon"]}

        password = secrets.randbits(63)
        cur = self.db.execute(
            """INSERT INTO wt_entries
               (password, owner_id, state, entered_at, pokemon, species)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (password, owner_id, STATE_WAITING, time.time(),
             pokemon_encrypted, pk.species),
        )
        self.db.commit()
        return {"matched": False, "entry_id": cur.lastrowid, "password": password}

    def collect(self, entry_id: int, password: int) -> dict:
        """Poll a waiting entry. Returns the partner's mon once matched and
        closes the entry."""
        row = self._get(entry_id, password)
        if row["state"] == STATE_WAITING:
            return {"matched": False}
        self.db.execute("DELETE FROM wt_entries WHERE entry_id = ?", (entry_id,))
        self.db.commit()
        return {"matched": True, "pokemon": row["result"]}

    def cancel(self, entry_id: int, password: int) -> bytes:
        """Withdraw an unmatched entry; returns the mon."""
        row = self._get(entry_id, password)
        if row["state"] != STATE_WAITING:
            raise WonderTradeError("gone", "entry already matched — collect it instead")
        self.db.execute("DELETE FROM wt_entries WHERE entry_id = ?", (entry_id,))
        self.db.commit()
        return row["pokemon"]

    def pool_size(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM wt_entries WHERE state = ?", (STATE_WAITING,)
        ).fetchone()[0]

    def _get(self, entry_id: int, password: int) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM wt_entries WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            raise WonderTradeError("not_found", f"no entry {entry_id}")
        if row["password"] != password:
            raise WonderTradeError("denied", "bad entry password")
        return row
