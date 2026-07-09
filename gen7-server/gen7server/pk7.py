"""PK7 codec — the 232-byte stored Pokémon format used by Gen 7 (SM/USUM).

Implements the format exactly as documented by the PKHeX project and
Project Pokémon's community research:

  * XOR stream cipher seeded by the encryption constant (GameFreak LCRNG)
  * 4x56-byte block shuffle selected by bits 13-17 of the encryption constant
  * 16-bit additive checksum over the 224-byte data region

A PK7 file on the wire (what the GTS stores and ships) is
``encrypt(shuffle(plaintext))``. All field accessors here operate on the
decrypted, unshuffled plaintext.

Nothing in this module is Nintendo code — the constants and layout are
long-published community documentation.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

SIZE_STORED = 232  # 0xE8 — box/GTS format
SIZE_PARTY = 260   # 0x104 — party format (stored + battle stats)

BLOCK_SIZE = 56
BLOCK_START = 8  # EC(4) + sanity(2) + checksum(2) precede the shuffled region
DATA_SIZE = BLOCK_SIZE * 4  # 224

LCRNG_MULT = 0x41C64E6D
LCRNG_ADD = 0x6073

# Block shuffle orders indexed by shift value sv = (EC >> 13) & 0x1F.
# Row sv lists, for each output slot, which plaintext block (0=A..3=D)
# occupies it in the shuffled layout. Rows 24-31 repeat rows 0-7.
_BLOCK_POSITION = [
    (0, 1, 2, 3), (0, 1, 3, 2), (0, 2, 1, 3), (0, 3, 1, 2),
    (0, 2, 3, 1), (0, 3, 2, 1), (1, 0, 2, 3), (1, 0, 3, 2),
    (2, 0, 1, 3), (3, 0, 1, 2), (2, 0, 3, 1), (3, 0, 2, 1),
    (1, 2, 0, 3), (1, 3, 0, 2), (2, 1, 0, 3), (3, 1, 0, 2),
    (2, 3, 0, 1), (3, 2, 0, 1), (1, 2, 3, 0), (1, 3, 2, 0),
    (2, 1, 3, 0), (3, 1, 2, 0), (2, 3, 1, 0), (3, 2, 1, 0),
    (0, 1, 2, 3), (0, 1, 3, 2), (0, 2, 1, 3), (0, 3, 1, 2),
    (0, 2, 3, 1), (0, 3, 2, 1), (1, 0, 2, 3), (1, 0, 3, 2),
]

# Inverse permutation index: applying _BLOCK_POSITION[_BLOCK_POSITION_INVERT[sv]]
# undoes _BLOCK_POSITION[sv]. Only defined for the 24 unique orders.
_BLOCK_POSITION_INVERT = [
    0, 1, 2, 4, 3, 5, 6, 7, 12, 18, 13, 19, 8, 10, 14, 20,
    16, 22, 9, 11, 15, 21, 17, 23,
]


def _crypt(data: bytearray, seed: int, start: int, end: int) -> None:
    """XOR the u16 stream from ``start`` to ``end`` with the LCRNG keystream."""
    for ofs in range(start, end, 2):
        seed = (seed * LCRNG_MULT + LCRNG_ADD) & 0xFFFFFFFF
        key = (seed >> 16) & 0xFFFF
        data[ofs] ^= key & 0xFF
        data[ofs + 1] ^= key >> 8


def _shuffle(data: bytes, sv: int) -> bytes:
    """Rearrange the four 56-byte blocks according to shift value ``sv``."""
    order = _BLOCK_POSITION[sv]
    out = bytearray(data)
    for slot, src_block in enumerate(order):
        src = BLOCK_START + BLOCK_SIZE * src_block
        dst = BLOCK_START + BLOCK_SIZE * slot
        out[dst:dst + BLOCK_SIZE] = data[src:src + BLOCK_SIZE]
    return bytes(out)


def encryption_constant(data: bytes) -> int:
    return struct.unpack_from("<I", data, 0)[0]


def shift_value(data: bytes) -> int:
    return (encryption_constant(data) >> 13) & 0x1F


def calculate_checksum(decrypted: bytes) -> int:
    """16-bit additive checksum over the 224-byte data region."""
    total = 0
    for ofs in range(BLOCK_START, SIZE_STORED, 2):
        total += struct.unpack_from("<H", decrypted, ofs)[0]
    return total & 0xFFFF


def decrypt(data: bytes) -> bytes:
    """Encrypted+shuffled wire format -> plaintext. Accepts stored size only."""
    if len(data) != SIZE_STORED:
        raise ValueError(f"expected {SIZE_STORED} bytes, got {len(data)}")
    buf = bytearray(data)
    ec = encryption_constant(data)
    _crypt(buf, ec, BLOCK_START, SIZE_STORED)
    return _shuffle(bytes(buf), shift_value(data))


def encrypt(data: bytes) -> bytes:
    """Plaintext -> encrypted+shuffled wire format."""
    if len(data) != SIZE_STORED:
        raise ValueError(f"expected {SIZE_STORED} bytes, got {len(data)}")
    sv = shift_value(data) % 24
    shuffled = bytearray(_shuffle(data, _BLOCK_POSITION_INVERT[sv]))
    _crypt(shuffled, encryption_constant(data), BLOCK_START, SIZE_STORED)
    return bytes(shuffled)


def is_encrypted(data: bytes) -> bool:
    """Heuristic used by the games: the sanity word is zero when decrypted,
    and a decrypted mon's checksum matches."""
    if struct.unpack_from("<H", data, 4)[0] != 0:
        return True
    return calculate_checksum(data) != struct.unpack_from("<H", data, 6)[0]


def _read_utf16(data: bytes, offset: int, max_chars: int) -> str:
    chars = []
    for i in range(max_chars):
        cp = struct.unpack_from("<H", data, offset + i * 2)[0]
        if cp == 0:
            break
        chars.append(chr(cp))
    return "".join(chars)


def _write_utf16(data: bytearray, offset: int, max_chars: int, text: str) -> None:
    if len(text) > max_chars:
        raise ValueError(f"string too long ({len(text)} > {max_chars})")
    for i in range(max_chars + 1):
        cp = ord(text[i]) if i < len(text) else 0
        struct.pack_into("<H", data, offset + i * 2, cp)


@dataclass
class PK7:
    """Parsed view over a decrypted 232-byte PK7. Offsets follow PKHeX's PK7 layout."""

    data: bytearray

    # -- construction -------------------------------------------------

    @classmethod
    def from_encrypted(cls, raw: bytes) -> "PK7":
        return cls(bytearray(decrypt(raw)))

    @classmethod
    def from_decrypted(cls, raw: bytes) -> "PK7":
        if len(raw) != SIZE_STORED:
            raise ValueError(f"expected {SIZE_STORED} bytes, got {len(raw)}")
        return cls(bytearray(raw))

    def to_encrypted(self) -> bytes:
        self.refresh_checksum()
        return encrypt(bytes(self.data))

    def to_decrypted(self) -> bytes:
        self.refresh_checksum()
        return bytes(self.data)

    # -- helpers ------------------------------------------------------

    def _u8(self, ofs: int) -> int:
        return self.data[ofs]

    def _set_u8(self, ofs: int, val: int) -> None:
        self.data[ofs] = val & 0xFF

    def _u16(self, ofs: int) -> int:
        return struct.unpack_from("<H", self.data, ofs)[0]

    def _set_u16(self, ofs: int, val: int) -> None:
        struct.pack_into("<H", self.data, ofs, val & 0xFFFF)

    def _u32(self, ofs: int) -> int:
        return struct.unpack_from("<I", self.data, ofs)[0]

    def _set_u32(self, ofs: int, val: int) -> None:
        struct.pack_into("<I", self.data, ofs, val & 0xFFFFFFFF)

    def refresh_checksum(self) -> None:
        self._set_u16(0x06, calculate_checksum(bytes(self.data)))

    @property
    def checksum_valid(self) -> bool:
        return self._u16(0x06) == calculate_checksum(bytes(self.data))

    # -- header -------------------------------------------------------

    @property
    def encryption_constant_(self) -> int:
        return self._u32(0x00)

    @encryption_constant_.setter
    def encryption_constant_(self, v: int) -> None:
        self._set_u32(0x00, v)

    @property
    def sanity(self) -> int:
        return self._u16(0x04)

    # -- block A: growth/attributes ------------------------------------

    @property
    def species(self) -> int:
        return self._u16(0x08)

    @species.setter
    def species(self, v: int) -> None:
        self._set_u16(0x08, v)

    @property
    def held_item(self) -> int:
        return self._u16(0x0A)

    @held_item.setter
    def held_item(self, v: int) -> None:
        self._set_u16(0x0A, v)

    @property
    def tid(self) -> int:
        return self._u16(0x0C)

    @tid.setter
    def tid(self, v: int) -> None:
        self._set_u16(0x0C, v)

    @property
    def sid(self) -> int:
        return self._u16(0x0E)

    @sid.setter
    def sid(self, v: int) -> None:
        self._set_u16(0x0E, v)

    @property
    def exp(self) -> int:
        return self._u32(0x10)

    @exp.setter
    def exp(self, v: int) -> None:
        self._set_u32(0x10, v)

    @property
    def ability(self) -> int:
        return self._u8(0x14)

    @ability.setter
    def ability(self, v: int) -> None:
        self._set_u8(0x14, v)

    @property
    def ability_number(self) -> int:
        return self._u8(0x15)

    @ability_number.setter
    def ability_number(self, v: int) -> None:
        self._set_u8(0x15, v)

    @property
    def pid(self) -> int:
        return self._u32(0x18)

    @pid.setter
    def pid(self, v: int) -> None:
        self._set_u32(0x18, v)

    @property
    def nature(self) -> int:
        return self._u8(0x1C)

    @nature.setter
    def nature(self, v: int) -> None:
        self._set_u8(0x1C, v)

    @property
    def gender(self) -> int:
        """0 = male, 1 = female, 2 = genderless (bits 1-2 of 0x1D)."""
        return (self._u8(0x1D) >> 1) & 0x3

    @gender.setter
    def gender(self, v: int) -> None:
        self._set_u8(0x1D, (self._u8(0x1D) & ~0x6) | ((v & 0x3) << 1))

    @property
    def form(self) -> int:
        return self._u8(0x1D) >> 3

    @form.setter
    def form(self, v: int) -> None:
        self._set_u8(0x1D, (self._u8(0x1D) & 0x7) | (v << 3))

    @property
    def evs(self) -> tuple[int, ...]:
        """(HP, Atk, Def, Spe, SpA, SpD) — game storage order."""
        return tuple(self.data[0x1E:0x24])

    @evs.setter
    def evs(self, values) -> None:
        if len(values) != 6:
            raise ValueError("need 6 EVs")
        self.data[0x1E:0x24] = bytes(values)

    # -- block B: moves/identity ---------------------------------------

    @property
    def nickname(self) -> str:
        return _read_utf16(bytes(self.data), 0x40, 12)

    @nickname.setter
    def nickname(self, v: str) -> None:
        _write_utf16(self.data, 0x40, 12, v)

    @property
    def moves(self) -> tuple[int, int, int, int]:
        return tuple(self._u16(0x5A + i * 2) for i in range(4))

    @moves.setter
    def moves(self, values) -> None:
        if len(values) != 4:
            raise ValueError("need 4 moves")
        for i, m in enumerate(values):
            self._set_u16(0x5A + i * 2, m)

    @property
    def relearn_moves(self) -> tuple[int, int, int, int]:
        return tuple(self._u16(0x6A + i * 2) for i in range(4))

    @property
    def iv32(self) -> int:
        return self._u32(0x74)

    @property
    def ivs(self) -> tuple[int, ...]:
        """(HP, Atk, Def, Spe, SpA, SpD) — 5 bits each, game storage order."""
        packed = self.iv32
        return tuple((packed >> (5 * i)) & 0x1F for i in range(6))

    @ivs.setter
    def ivs(self, values) -> None:
        if len(values) != 6:
            raise ValueError("need 6 IVs")
        packed = self.iv32 & 0xC0000000  # preserve egg/nickname flags
        for i, iv in enumerate(values):
            packed |= (iv & 0x1F) << (5 * i)
        self._set_u32(0x74, packed)

    @property
    def is_egg(self) -> bool:
        return bool((self.iv32 >> 30) & 1)

    @is_egg.setter
    def is_egg(self, v: bool) -> None:
        self._set_u32(0x74, (self.iv32 & ~(1 << 30)) | (int(v) << 30))

    @property
    def is_nicknamed(self) -> bool:
        return bool((self.iv32 >> 31) & 1)

    # -- block D: OT / origins ------------------------------------------

    @property
    def ot_name(self) -> str:
        return _read_utf16(bytes(self.data), 0xB0, 12)

    @ot_name.setter
    def ot_name(self, v: str) -> None:
        _write_utf16(self.data, 0xB0, 12, v)

    @property
    def ball(self) -> int:
        return self._u8(0xDC)

    @ball.setter
    def ball(self, v: int) -> None:
        self._set_u8(0xDC, v)

    @property
    def met_level(self) -> int:
        return self._u8(0xDD) & 0x7F

    @met_level.setter
    def met_level(self, v: int) -> None:
        self._set_u8(0xDD, (self._u8(0xDD) & 0x80) | (v & 0x7F))

    @property
    def ot_gender(self) -> int:
        return self._u8(0xDD) >> 7

    @property
    def version(self) -> int:
        """Origin game (30 = Sun, 31 = Moon, 32 = Ultra Sun, 33 = Ultra Moon)."""
        return self._u8(0xDF)

    @version.setter
    def version(self, v: int) -> None:
        self._set_u8(0xDF, v)

    @property
    def language(self) -> int:
        return self._u8(0xE3)

    @language.setter
    def language(self, v: int) -> None:
        self._set_u8(0xE3, v)

    # -- derived --------------------------------------------------------

    @property
    def is_shiny(self) -> bool:
        """Gen 6+ rule: (TID ^ SID ^ PID_hi ^ PID_lo) < 16."""
        xor = self.tid ^ self.sid ^ (self.pid >> 16) ^ (self.pid & 0xFFFF)
        return xor < 16

    def summary(self) -> dict:
        """JSON-friendly digest used by the GTS index and API responses."""
        return {
            "species": self.species,
            "form": self.form,
            "gender": self.gender,
            "nature": self.nature,
            "held_item": self.held_item,
            "ability": self.ability,
            "nickname": self.nickname,
            "ot_name": self.ot_name,
            "tid": self.tid,
            "sid": self.sid,
            "moves": list(self.moves),
            "ivs": list(self.ivs),
            "evs": list(self.evs),
            "ball": self.ball,
            "met_level": self.met_level,
            "version": self.version,
            "language": self.language,
            "is_egg": self.is_egg,
            "is_shiny": self.is_shiny,
        }
