import time

import pytest

from gen7server.mystery_gift import MysteryGift, MysteryGiftError

CARD = b"\x01" * 0x310  # stand-in wondercard blob
USER = 42


@pytest.fixture
def mg():
    return MysteryGift(":memory:")


def test_internet_distribution(mg):
    gid = mg.add_gift("Shiny Tapu Koko", CARD)
    assert mg.list_live() == [{"gift_id": gid, "title": "Shiny Tapu Koko"}]
    assert mg.download(gid, USER) == CARD


def test_one_redemption_per_user(mg):
    gid = mg.add_gift("Shiny Tapu Koko", CARD)
    mg.download(gid, USER)
    with pytest.raises(MysteryGiftError) as e:
        mg.download(gid, USER)
    assert e.value.code == "used"
    # other users unaffected
    assert mg.download(gid, USER + 1) == CARD


def test_delivery_window(mg):
    now = time.time()
    gid = mg.add_gift("Past event", CARD, live_from=now - 100, live_until=now - 50)
    assert mg.list_live() == []
    with pytest.raises(MysteryGiftError) as e:
        mg.download(gid, USER)
    assert e.value.code == "not_live"

    future = mg.add_gift("Future event", CARD, live_from=now + 1000)
    assert mg.list_live() == []
    with pytest.raises(MysteryGiftError):
        mg.download(future, USER)


def test_serial_code_flow(mg):
    gid = mg.add_gift("Marshadow", CARD, via_internet=False)
    assert mg.list_live() == []  # code-gated gifts hidden from internet list
    mg.add_serial_code("MARSHADOW20", gid)

    assert mg.redeem_code("marshadow20", USER) == CARD  # case-insensitive
    with pytest.raises(MysteryGiftError) as e:
        mg.redeem_code("MARSHADOW20", USER + 1)
    assert e.value.code == "used"  # single-use code


def test_multi_use_code(mg):
    gid = mg.add_gift("Event", CARD, via_internet=False)
    mg.add_serial_code("SHARED", gid, max_uses=2)
    mg.redeem_code("SHARED", 1)
    mg.redeem_code("SHARED", 2)
    with pytest.raises(MysteryGiftError):
        mg.redeem_code("SHARED", 3)


def test_unknown_code(mg):
    with pytest.raises(MysteryGiftError) as e:
        mg.redeem_code("NOPE", USER)
    assert e.value.code == "bad_code"
