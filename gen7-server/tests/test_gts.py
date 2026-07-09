import pytest

from gen7server.factory import make_pk7
from gen7server.gts import GTS, GTSError, RecordKey
from gen7server.pk7 import PK7

ALICE, BOB = 1001, 1002


@pytest.fixture
def gts():
    return GTS(":memory:")


def deposit(gts, owner, mon=None, **request):
    mon = mon or make_pk7(species=25, nickname="Pikachu")
    ticket = gts.prepare_upload(owner)
    request.setdefault("requested_species", 133)  # wants an Eevee
    return gts.upload(owner, ticket, mon.to_encrypted(), **request)


def test_full_trade_flow(gts):
    # Alice deposits a Pikachu asking for an Eevee
    key = deposit(gts, ALICE)

    # Bob searches and finds it
    results = gts.search(species=25)
    assert len(results) == 1
    assert results[0]["req_species"] == 133

    # Bob offers an Eevee
    eevee = make_pk7(species=133, nickname="Eevee", ot_name="Bob", level=30)
    ticket, preview = gts.prepare_trade(BOB, results[0]["data_id"])
    assert preview["species"] == 25
    received = gts.trade(BOB, ticket, eevee.to_encrypted())
    assert PK7.from_encrypted(received).species == 25  # Bob got the Pikachu

    # Traded record no longer searchable
    assert gts.search(species=25) == []

    # Alice collects the Eevee
    outcome = gts.download_my(key)
    assert outcome["traded"] is True
    assert PK7.from_encrypted(outcome["pokemon"]).species == 133

    # Record fully closed afterwards
    with pytest.raises(GTSError):
        gts.download_my(key)


def test_trade_rejects_wrong_species(gts):
    key = deposit(gts, ALICE)  # wants Eevee (133)
    magikarp = make_pk7(species=129, nickname="Magikarp")
    ticket, _ = gts.prepare_trade(BOB, key.data_id)
    with pytest.raises(GTSError) as e:
        gts.trade(BOB, ticket, magikarp.to_encrypted())
    assert e.value.code == "mismatch"
    # Deposit is still up for grabs after the failed trade
    assert len(gts.search(species=25)) == 1


def test_trade_rejects_level_out_of_range(gts):
    key = deposit(gts, ALICE, requested_min_level=50, requested_max_level=100)
    low_eevee = make_pk7(species=133, level=10)
    ticket, _ = gts.prepare_trade(BOB, key.data_id)
    with pytest.raises(GTSError) as e:
        gts.trade(BOB, ticket, low_eevee.to_encrypted())
    assert e.value.code == "mismatch"


def test_cannot_trade_own_deposit(gts):
    key = deposit(gts, ALICE)
    with pytest.raises(GTSError) as e:
        gts.prepare_trade(ALICE, key.data_id)
    assert e.value.code == "own_record"


def test_withdraw(gts):
    key = deposit(gts, ALICE)
    blob = gts.delete(key)
    assert PK7.from_encrypted(blob).species == 25
    assert gts.search(species=25) == []


def test_withdraw_requires_password(gts):
    key = deposit(gts, ALICE)
    with pytest.raises(GTSError) as e:
        gts.delete(RecordKey(key.data_id, key.password ^ 1))
    assert e.value.code == "denied"


def test_double_trade_race(gts):
    """Two traders prepare against the same record; only the first completes."""
    key = deposit(gts, ALICE)
    carol = 1003
    t_bob, _ = gts.prepare_trade(BOB, key.data_id)
    t_carol, _ = gts.prepare_trade(carol, key.data_id)

    eevee1 = make_pk7(species=133, ot_name="Bob")
    eevee2 = make_pk7(species=133, ot_name="Carol")
    gts.trade(BOB, t_bob, eevee1.to_encrypted())
    with pytest.raises(GTSError) as e:
        gts.trade(carol, t_carol, eevee2.to_encrypted())
    assert e.value.code == "gone"


def test_upload_rejects_garbage(gts):
    ticket = gts.prepare_upload(ALICE)
    with pytest.raises(GTSError):
        gts.upload(ALICE, ticket, b"\x00" * 100, requested_species=1)


def test_upload_rejects_corrupt_checksum(gts):
    mon = make_pk7()
    wire = bytearray(mon.to_encrypted())
    wire[100] ^= 0xFF  # corrupt inside the encrypted region
    ticket = gts.prepare_upload(ALICE)
    with pytest.raises(GTSError) as e:
        gts.upload(ALICE, ticket, bytes(wire), requested_species=1)
    assert e.value.code == "illegal"


def test_upload_rejects_egg(gts):
    egg = make_pk7()
    egg.is_egg = True
    ticket = gts.prepare_upload(ALICE)
    with pytest.raises(GTSError) as e:
        gts.upload(ALICE, ticket, egg.to_encrypted(), requested_species=1)
    assert e.value.code == "illegal"


def test_ticket_single_use(gts):
    mon = make_pk7()
    ticket = gts.prepare_upload(ALICE)
    gts.upload(ALICE, ticket, mon.to_encrypted(), requested_species=1)
    with pytest.raises(GTSError) as e:
        gts.upload(ALICE, ticket, mon.to_encrypted(), requested_species=1)
    assert e.value.code == "bad_ticket"


def test_ticket_owner_bound(gts):
    ticket = gts.prepare_upload(ALICE)
    with pytest.raises(GTSError) as e:
        gts.upload(BOB, ticket, make_pk7().to_encrypted(), requested_species=1)
    assert e.value.code == "bad_ticket"


def test_search_filters(gts):
    deposit(gts, ALICE, make_pk7(species=25, gender=0, level=50))
    deposit(gts, ALICE, make_pk7(species=25, gender=1, level=10))
    deposit(gts, BOB, make_pk7(species=6, level=60, shiny=True))

    assert len(gts.search()) == 3
    assert len(gts.search(species=25)) == 2
    assert len(gts.search(species=25, gender=1)) == 1
    assert len(gts.search(min_level=40)) == 2
    assert len(gts.search(shiny_only=True)) == 1
    assert len(gts.search(exclude_owner=ALICE)) == 1


def test_search_pagination(gts):
    for _ in range(5):
        deposit(gts, ALICE)
    page1 = gts.search(species=25, limit=2, offset=0)
    page2 = gts.search(species=25, limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert {r["data_id"] for r in page1}.isdisjoint({r["data_id"] for r in page2})
