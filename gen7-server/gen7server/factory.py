"""Construct valid PK7 blobs from scratch — for tests, demos, and seeding.

This builds structurally valid Pokémon (correct checksum, sane fields).
It makes no claim of game-legality beyond what legality.py checks; it
exists so the server can be exercised without touching real save data.
"""

from __future__ import annotations

import secrets

from .pk7 import PK7, SIZE_STORED

VERSION_ULTRA_SUN = 32
VERSION_ULTRA_MOON = 33


def make_pk7(
    *,
    species: int = 25,           # Pikachu
    nickname: str = "Pikachu",
    ot_name: str = "Trainer",
    tid: int = 12345,
    sid: int = 54321,
    level: int = 50,
    nature: int = 10,            # Timid
    gender: int = 0,
    moves: tuple[int, int, int, int] = (85, 86, 87, 98),  # electric kit + quick attack
    ivs: tuple[int, ...] = (31, 31, 31, 31, 31, 31),
    evs: tuple[int, ...] = (0, 0, 0, 252, 252, 6),
    ball: int = 4,               # Poké Ball
    version: int = VERSION_ULTRA_SUN,
    language: int = 2,           # English
    held_item: int = 0,
    shiny: bool = False,
    encryption_constant: int | None = None,
    pid: int | None = None,
) -> PK7:
    pk = PK7(bytearray(SIZE_STORED))
    pk.encryption_constant_ = (
        encryption_constant if encryption_constant is not None else secrets.randbits(32)
    )
    if pid is None:
        pid = secrets.randbits(32)
        if shiny:
            # Force the shiny xor to zero: pid_hi = tid ^ sid ^ pid_lo
            pid = ((tid ^ sid ^ (pid & 0xFFFF)) << 16) | (pid & 0xFFFF)
        else:
            while (tid ^ sid ^ (pid >> 16) ^ (pid & 0xFFFF)) < 16:
                pid = secrets.randbits(32)
    pk.pid = pid
    pk.species = species
    pk.held_item = held_item
    pk.tid = tid
    pk.sid = sid
    pk.exp = 125000  # roughly mid-game; not level-derived (no growth tables here)
    pk.ability = 9   # Static
    pk.ability_number = 1
    pk.nature = nature
    pk.gender = gender
    pk.evs = evs
    pk.ivs = ivs
    pk.nickname = nickname
    pk.moves = moves
    pk.ot_name = ot_name
    pk.ball = ball
    pk.met_level = level
    pk.version = version
    pk.language = language
    pk.refresh_checksum()
    return pk
