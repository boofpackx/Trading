"""GTS (Global Trade System) backend.

The API surface deliberately mirrors the eight custom DataStore methods the
Gen 6/7 games call on the real servers (NEX DataStore protocol 115, methods
40-47, documented on Kinnay's NintendoClients wiki):

    40 PrepareUploadPokemon   -> prepare_upload()
    41 UploadPokemon          -> upload()
    42 SearchPokemon          -> search()
    43 PrepareTradePokemon    -> prepare_trade()
    44 TradePokemon           -> trade()
    45 DownloadOtherPokemon   -> download_other()
    46 DownloadMyPokemon      -> download_my()
    47 DeletePokemon          -> delete()

Keeping a 1:1 mapping means a future NEX transport layer (e.g. built on
Pretendo's nex-go) only has to deserialize the request structure and call
the matching method here — no logic reshuffling.

Storage is SQLite; deposited Pokémon are stored encrypted exactly as the
game ships them, alongside a decrypted search index.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from dataclasses import dataclass

from . import legality
from .pk7 import PK7, SIZE_STORED

# Record lifecycle
STATE_DEPOSITED = 0   # visible in searches, waiting for a trade
STATE_TRADED = 1      # partner took it; result waits for the depositor
STATE_WITHDRAWN = 2   # owner deleted it


class GTSError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class RecordKey:
    """Mirrors GlobalTradeStationRecordKey: data_id + password."""
    data_id: int
    password: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS gts_records (
    data_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    password    INTEGER NOT NULL,
    owner_id    INTEGER NOT NULL,
    state       INTEGER NOT NULL DEFAULT 0,
    uploaded_at REAL NOT NULL,
    traded_at   REAL,
    -- the deposited mon, encrypted wire format
    pokemon     BLOB NOT NULL,
    -- what the partner sent in exchange (encrypted), once traded
    result      BLOB,
    -- search index (decrypted metadata of the deposited mon)
    species     INTEGER NOT NULL,
    form        INTEGER NOT NULL,
    gender      INTEGER NOT NULL,
    level       INTEGER NOT NULL,
    is_shiny    INTEGER NOT NULL,
    ot_name     TEXT NOT NULL,
    -- what the depositor asked for
    req_species INTEGER NOT NULL,
    req_gender  INTEGER NOT NULL,   -- 0 male, 1 female, 2 either
    req_min_lvl INTEGER NOT NULL,
    req_max_lvl INTEGER NOT NULL,
    message     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_gts_search
    ON gts_records (state, species, gender, level);
CREATE TABLE IF NOT EXISTS upload_tickets (
    ticket     INTEGER PRIMARY KEY,
    owner_id   INTEGER NOT NULL,
    issued_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_tickets (
    ticket     INTEGER PRIMARY KEY,
    owner_id   INTEGER NOT NULL,
    data_id    INTEGER NOT NULL,
    issued_at  REAL NOT NULL
);
"""

TICKET_TTL = 600  # seconds a prepare-* ticket stays valid


class GTS:
    def __init__(self, db_path: str = ":memory:", *, require_legal: bool = True):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.require_legal = require_legal

    # -- 40: PrepareUploadPokemon ---------------------------------------

    def prepare_upload(self, owner_id: int) -> int:
        """Issue an upload ticket. The real server hands back signed upload
        credentials; we model that as a one-time ticket."""
        ticket = secrets.randbits(63)
        self.db.execute(
            "INSERT INTO upload_tickets (ticket, owner_id, issued_at) VALUES (?, ?, ?)",
            (ticket, owner_id, time.time()),
        )
        self.db.commit()
        return ticket

    # -- 41: UploadPokemon ------------------------------------------------

    def upload(
        self,
        owner_id: int,
        ticket: int,
        pokemon_encrypted: bytes,
        *,
        requested_species: int,
        requested_gender: int = 2,
        requested_min_level: int = 1,
        requested_max_level: int = 100,
        message: str = "",
    ) -> RecordKey:
        self._consume_ticket("upload_tickets", ticket, owner_id)

        if len(pokemon_encrypted) != SIZE_STORED:
            raise GTSError("bad_size", f"pokemon must be {SIZE_STORED} bytes")
        pk = PK7.from_encrypted(pokemon_encrypted)
        if self.require_legal:
            problems = legality.check(pk)
            if problems:
                raise GTSError("illegal", "; ".join(problems))
        if pk.is_egg:
            raise GTSError("illegal", "eggs cannot be deposited on the GTS")

        password = secrets.randbits(63)
        cur = self.db.execute(
            """INSERT INTO gts_records
               (password, owner_id, state, uploaded_at, pokemon,
                species, form, gender, level, is_shiny, ot_name,
                req_species, req_gender, req_min_lvl, req_max_lvl, message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                password, owner_id, STATE_DEPOSITED, time.time(),
                pokemon_encrypted,
                pk.species, pk.form, pk.gender, pk.met_level, int(pk.is_shiny),
                pk.ot_name,
                requested_species, requested_gender,
                requested_min_level, requested_max_level, message,
            ),
        )
        self.db.commit()
        return RecordKey(data_id=cur.lastrowid, password=password)

    # -- 42: SearchPokemon --------------------------------------------------

    def search(
        self,
        *,
        species: int | None = None,
        gender: int | None = None,
        min_level: int = 1,
        max_level: int = 100,
        shiny_only: bool = False,
        exclude_owner: int | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[dict]:
        """Return summaries of deposited mons matching the filters, newest first."""
        clauses = ["state = ?"]
        params: list = [STATE_DEPOSITED]
        if species is not None:
            clauses.append("species = ?")
            params.append(species)
        if gender is not None:
            clauses.append("gender = ?")
            params.append(gender)
        clauses.append("level BETWEEN ? AND ?")
        params.extend([min_level, max_level])
        if shiny_only:
            clauses.append("is_shiny = 1")
        if exclude_owner is not None:
            clauses.append("owner_id != ?")
            params.append(exclude_owner)
        params.extend([min(max(limit, 1), 100), max(offset, 0)])

        rows = self.db.execute(
            f"""SELECT data_id, species, form, gender, level, is_shiny, ot_name,
                       req_species, req_gender, req_min_lvl, req_max_lvl,
                       message, uploaded_at
                FROM gts_records WHERE {' AND '.join(clauses)}
                ORDER BY uploaded_at DESC LIMIT ? OFFSET ?""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    # -- 43: PrepareTradePokemon ---------------------------------------------

    def prepare_trade(self, trader_id: int, data_id: int) -> tuple[int, dict]:
        """Lock in intent to trade for a record; returns (trade ticket, summary)."""
        row = self._get_record(data_id)
        if row["state"] != STATE_DEPOSITED:
            raise GTSError("gone", "record is no longer available")
        if row["owner_id"] == trader_id:
            raise GTSError("own_record", "cannot trade for your own deposit")

        ticket = secrets.randbits(63)
        self.db.execute(
            "INSERT INTO trade_tickets (ticket, owner_id, data_id, issued_at) VALUES (?, ?, ?, ?)",
            (ticket, trader_id, data_id, time.time()),
        )
        self.db.commit()
        summary = PK7.from_encrypted(row["pokemon"]).summary()
        return ticket, summary

    # -- 44: TradePokemon -------------------------------------------------------

    def trade(self, trader_id: int, ticket: int, offered_encrypted: bytes) -> bytes:
        """Complete a trade: the offered mon must satisfy the depositor's request.
        Returns the deposited mon (encrypted); the offer is parked on the record
        for the depositor to pick up via download_my()."""
        data_id = self._consume_ticket("trade_tickets", ticket, trader_id)
        row = self._get_record(data_id)
        if row["state"] != STATE_DEPOSITED:
            raise GTSError("gone", "record was taken by someone else")

        offered = PK7.from_encrypted(offered_encrypted)
        if self.require_legal:
            problems = legality.check(offered)
            if problems:
                raise GTSError("illegal", "; ".join(problems))
        if offered.is_egg:
            raise GTSError("illegal", "eggs cannot be traded on the GTS")
        self._check_request(row, offered)

        self.db.execute(
            "UPDATE gts_records SET state = ?, result = ?, traded_at = ? WHERE data_id = ?",
            (STATE_TRADED, offered_encrypted, time.time(), data_id),
        )
        self.db.commit()
        return row["pokemon"]

    # -- 45: DownloadOtherPokemon -----------------------------------------------

    def download_other(self, data_id: int) -> bytes:
        """Fetch a deposited mon's data for preview (the game renders the model
        from the full pkm blob)."""
        row = self._get_record(data_id)
        if row["state"] != STATE_DEPOSITED:
            raise GTSError("gone", "record is no longer available")
        return row["pokemon"]

    # -- 46: DownloadMyPokemon -----------------------------------------------------

    def download_my(self, key: RecordKey) -> dict:
        """Depositor checks on their record. If traded, returns the received mon
        and closes the record."""
        row = self._get_record(key.data_id)
        if row["password"] != key.password:
            raise GTSError("denied", "bad record password")
        if row["state"] == STATE_DEPOSITED:
            return {"traded": False}
        if row["state"] == STATE_TRADED and row["result"] is not None:
            self.db.execute(
                "DELETE FROM gts_records WHERE data_id = ?", (key.data_id,)
            )
            self.db.commit()
            return {"traded": True, "pokemon": row["result"]}
        raise GTSError("gone", "record already closed")

    # -- 47: DeletePokemon ------------------------------------------------------------

    def delete(self, key: RecordKey) -> bytes:
        """Withdraw an untraded deposit; returns the mon so the game can
        restore it to the box."""
        row = self._get_record(key.data_id)
        if row["password"] != key.password:
            raise GTSError("denied", "bad record password")
        if row["state"] != STATE_DEPOSITED:
            raise GTSError("gone", "record already traded or withdrawn")
        self.db.execute(
            "DELETE FROM gts_records WHERE data_id = ?", (key.data_id,)
        )
        self.db.commit()
        return row["pokemon"]

    # -- internals -----------------------------------------------------

    def _get_record(self, data_id: int) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM gts_records WHERE data_id = ?", (data_id,)
        ).fetchone()
        if row is None:
            raise GTSError("not_found", f"no record {data_id}")
        return row

    def _consume_ticket(self, table: str, ticket: int, owner_id: int) -> int | None:
        assert table in ("upload_tickets", "trade_tickets")
        row = self.db.execute(
            f"SELECT * FROM {table} WHERE ticket = ?", (ticket,)
        ).fetchone()
        if row is None or row["owner_id"] != owner_id:
            raise GTSError("bad_ticket", "unknown or foreign ticket")
        if time.time() - row["issued_at"] > TICKET_TTL:
            self.db.execute(f"DELETE FROM {table} WHERE ticket = ?", (ticket,))
            self.db.commit()
            raise GTSError("bad_ticket", "ticket expired")
        self.db.execute(f"DELETE FROM {table} WHERE ticket = ?", (ticket,))
        self.db.commit()
        return row["data_id"] if table == "trade_tickets" else None

    @staticmethod
    def _check_request(row: sqlite3.Row, offered: PK7) -> None:
        if row["req_species"] != 0 and offered.species != row["req_species"]:
            raise GTSError("mismatch", "offered species does not match request")
        if row["req_gender"] in (0, 1) and offered.gender != row["req_gender"]:
            raise GTSError("mismatch", "offered gender does not match request")
        if not (row["req_min_lvl"] <= offered.met_level <= row["req_max_lvl"]):
            raise GTSError("mismatch", "offered level outside requested range")
