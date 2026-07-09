import struct

import pytest

from gen7server import pk7
from gen7server.factory import make_pk7
from gen7server.pk7 import PK7, SIZE_STORED


def test_roundtrip_encrypt_decrypt():
    mon = make_pk7()
    plain = mon.to_decrypted()
    wire = pk7.encrypt(plain)
    assert wire != plain
    assert pk7.decrypt(wire) == plain


@pytest.mark.parametrize("ec", [0, 1, 0xFFFFFFFF, 0x12345678, 0xDEADBEEF])
def test_roundtrip_across_shift_values(ec):
    mon = make_pk7(encryption_constant=ec)
    plain = mon.to_decrypted()
    assert pk7.decrypt(pk7.encrypt(plain)) == plain


def test_all_32_shift_values_roundtrip():
    # sv = (EC >> 13) & 0x1F — construct one EC per shift value
    for sv in range(32):
        ec = sv << 13
        mon = make_pk7(encryption_constant=ec)
        plain = mon.to_decrypted()
        assert pk7.shift_value(plain) == sv
        assert pk7.decrypt(pk7.encrypt(plain)) == plain


def test_shuffle_permutes_whole_blocks():
    mon = make_pk7(encryption_constant=6 << 13)  # sv=6 -> order (1,0,2,3)
    plain = mon.to_decrypted()
    shuffled = pk7._shuffle(plain, 6)
    # header untouched
    assert shuffled[:8] == plain[:8]
    # blocks A and B swapped, C and D in place
    a, b, c, d = (plain[8 + 56 * i:8 + 56 * (i + 1)] for i in range(4))
    assert shuffled[8:64] == b
    assert shuffled[64:120] == a
    assert shuffled[120:176] == c
    assert shuffled[176:232] == d


def test_checksum_detects_corruption():
    mon = make_pk7()
    assert mon.checksum_valid
    mon.data[0x08] ^= 0xFF
    assert not mon.checksum_valid


def test_encrypted_wire_has_correct_checksum_after_decrypt():
    wire = make_pk7().to_encrypted()
    decrypted = pk7.decrypt(wire)
    stored = struct.unpack_from("<H", decrypted, 6)[0]
    assert stored == pk7.calculate_checksum(decrypted)


def test_is_encrypted_heuristic():
    mon = make_pk7()
    assert not pk7.is_encrypted(mon.to_decrypted())
    assert pk7.is_encrypted(mon.to_encrypted())


def test_field_accessors_roundtrip():
    mon = make_pk7(
        species=778,  # Mimikyu
        nickname="Mimikyu",
        ot_name="Lillie",
        tid=111,
        sid=222,
        level=44,
        nature=3,
        gender=1,
        moves=(87, 85, 84, 86),
        ivs=(1, 2, 3, 4, 5, 6),
        evs=(4, 8, 12, 16, 20, 24),
        ball=2,
        language=5,
    )
    wire = mon.to_encrypted()
    back = PK7.from_encrypted(wire)
    assert back.species == 778
    assert back.nickname == "Mimikyu"
    assert back.ot_name == "Lillie"
    assert (back.tid, back.sid) == (111, 222)
    assert back.met_level == 44
    assert back.nature == 3
    assert back.gender == 1
    assert back.moves == (87, 85, 84, 86)
    assert back.ivs == (1, 2, 3, 4, 5, 6)
    assert back.evs == (4, 8, 12, 16, 20, 24)
    assert back.ball == 2
    assert back.language == 5
    assert back.checksum_valid


def test_shiny_flag():
    shiny = make_pk7(shiny=True)
    assert shiny.is_shiny
    normal = make_pk7(shiny=False)
    assert not normal.is_shiny


def test_egg_flag_preserved_with_ivs():
    mon = make_pk7()
    mon.is_egg = True
    mon.ivs = (31, 0, 31, 0, 31, 0)
    assert mon.is_egg
    assert mon.ivs == (31, 0, 31, 0, 31, 0)


def test_size_validation():
    with pytest.raises(ValueError):
        pk7.decrypt(b"\x00" * (SIZE_STORED - 1))
    with pytest.raises(ValueError):
        pk7.encrypt(b"\x00" * (SIZE_STORED + 1))
