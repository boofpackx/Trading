"""Server-side sanity checks for incoming Pokémon.

The real Game Freak servers rejected structurally invalid uploads (and the
PGL ran deeper legality analysis). This module implements the structural
tier: cheap, deterministic checks that keep corrupt or absurd data out of
the GTS/Wonder Trade pools. It is intentionally not a full legality engine
— that niche is served by PKHeX's ALM; a hook is provided for wiring an
external checker in later (Pretendo runs one as a separate service, PKHaX).
"""

from __future__ import annotations

from .pk7 import PK7

# Gen 7 (USUM) national dex tops out at 807 (Zeraora).
MAX_SPECIES = 807
# Highest Gen 7 move ID is 728 (Clangorous Soulblaze).
MAX_MOVE = 728
# USUM item table upper bound.
MAX_ITEM = 959
MAX_BALL = 26  # Beast Ball
MAX_EV_SINGLE = 252
MAX_EV_TOTAL = 510
VALID_VERSIONS = {30, 31, 32, 33}  # Sun, Moon, Ultra Sun, Ultra Moon
# Gen 7 GTS also accepted mons originating from older games via Bank;
# widen when Bank-transfer support is added.


class LegalityError(ValueError):
    pass


def check(pk: PK7, *, require_gen7_origin: bool = False) -> list[str]:
    """Return a list of problems; empty list means structurally acceptable."""
    problems: list[str] = []

    if not pk.checksum_valid:
        problems.append("checksum mismatch")
    if pk.sanity != 0:
        problems.append("sanity bytes non-zero (data not decrypted correctly?)")
    if not (1 <= pk.species <= MAX_SPECIES):
        problems.append(f"species {pk.species} out of range 1..{MAX_SPECIES}")
    if pk.held_item > MAX_ITEM:
        problems.append(f"held item {pk.held_item} out of range")
    if pk.ball == 0 or pk.ball > MAX_BALL:
        problems.append(f"ball {pk.ball} invalid")
    if require_gen7_origin and pk.version not in VALID_VERSIONS:
        problems.append(f"origin version {pk.version} is not a Gen 7 game")

    moves = [m for m in pk.moves if m != 0]
    if not moves:
        problems.append("no moves")
    if any(m > MAX_MOVE for m in pk.moves):
        problems.append("move ID out of range")

    if any(ev > MAX_EV_SINGLE for ev in pk.evs):
        problems.append("single EV above 252")
    if sum(pk.evs) > MAX_EV_TOTAL:
        problems.append(f"EV total {sum(pk.evs)} above {MAX_EV_TOTAL}")

    if not pk.nickname:
        problems.append("empty nickname")
    if not pk.ot_name:
        problems.append("empty OT name")

    return problems


def assert_legal(pk: PK7, **kwargs) -> None:
    problems = check(pk, **kwargs)
    if problems:
        raise LegalityError("; ".join(problems))
