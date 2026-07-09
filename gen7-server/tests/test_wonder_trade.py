import pytest

from gen7server.factory import make_pk7
from gen7server.pk7 import PK7
from gen7server.wonder_trade import WonderTrade, WonderTradeError

ALICE, BOB, CAROL = 1, 2, 3


@pytest.fixture
def wt():
    return WonderTrade(":memory:")


def test_immediate_match(wt):
    pika = make_pk7(species=25, ot_name="Alice")
    eevee = make_pk7(species=133, ot_name="Bob")

    r1 = wt.enter(ALICE, pika.to_encrypted())
    assert r1["matched"] is False
    assert wt.pool_size() == 1

    r2 = wt.enter(BOB, eevee.to_encrypted())
    assert r2["matched"] is True
    assert PK7.from_encrypted(r2["pokemon"]).species == 25  # Bob got the Pikachu

    r3 = wt.collect(r1["entry_id"], r1["password"])
    assert r3["matched"] is True
    assert PK7.from_encrypted(r3["pokemon"]).species == 133  # Alice got the Eevee
    assert wt.pool_size() == 0


def test_never_matches_self(wt):
    r1 = wt.enter(ALICE, make_pk7(species=25).to_encrypted())
    r2 = wt.enter(ALICE, make_pk7(species=133).to_encrypted())
    assert r1["matched"] is False and r2["matched"] is False
    assert wt.pool_size() == 2
    # a different user matches the oldest entry first (FIFO)
    r3 = wt.enter(BOB, make_pk7(species=6).to_encrypted())
    assert r3["matched"] is True
    assert PK7.from_encrypted(r3["pokemon"]).species == 25


def test_collect_before_match(wt):
    r = wt.enter(ALICE, make_pk7().to_encrypted())
    assert wt.collect(r["entry_id"], r["password"]) == {"matched": False}
    # still in the pool
    assert wt.pool_size() == 1


def test_cancel(wt):
    r = wt.enter(ALICE, make_pk7(species=25).to_encrypted())
    blob = wt.cancel(r["entry_id"], r["password"])
    assert PK7.from_encrypted(blob).species == 25
    assert wt.pool_size() == 0


def test_cancel_after_match_fails(wt):
    r = wt.enter(ALICE, make_pk7(species=25).to_encrypted())
    wt.enter(BOB, make_pk7(species=133).to_encrypted())
    with pytest.raises(WonderTradeError) as e:
        wt.cancel(r["entry_id"], r["password"])
    assert e.value.code == "gone"
    # but collecting works
    assert wt.collect(r["entry_id"], r["password"])["matched"] is True


def test_password_required(wt):
    r = wt.enter(ALICE, make_pk7().to_encrypted())
    with pytest.raises(WonderTradeError) as e:
        wt.collect(r["entry_id"], r["password"] ^ 1)
    assert e.value.code == "denied"


def test_rejects_egg(wt):
    egg = make_pk7()
    egg.is_egg = True
    with pytest.raises(WonderTradeError) as e:
        wt.enter(ALICE, egg.to_encrypted())
    assert e.value.code == "illegal"


def test_three_way_fifo(wt):
    r_a = wt.enter(ALICE, make_pk7(species=1).to_encrypted())
    r_b = wt.enter(BOB, make_pk7(species=4).to_encrypted())  # matches Alice
    assert r_b["matched"] is True
    r_c = wt.enter(CAROL, make_pk7(species=7).to_encrypted())
    assert r_c["matched"] is False  # nobody left waiting
    got_a = wt.collect(r_a["entry_id"], r_a["password"])
    assert PK7.from_encrypted(got_a["pokemon"]).species == 4
