"""End-to-end test: two simulated players complete a GTS trade and a Wonder
Trade over the HTTP API against a live server socket."""

import base64
import json
import urllib.request

import pytest

from gen7server.api import Gen7Server
from gen7server.factory import make_pk7
from gen7server.pk7 import PK7

ALICE, BOB = 1, 2


@pytest.fixture
def server():
    srv = Gen7Server(":memory:")
    httpd = srv.serve(port=0)  # OS-assigned free port
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base
    srv.shutdown()


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def b64(pk):
    return base64.b64encode(pk.to_encrypted()).decode()


def from_b64(text):
    return PK7.from_encrypted(base64.b64decode(text))


def test_gts_trade_over_http(server):
    # Alice deposits a Pikachu, wants Eevee level 1-100
    _, t = call(server, "POST", "/gts/prepare_upload", {"user_id": ALICE})
    status, dep = call(server, "POST", "/gts/upload", {
        "user_id": ALICE,
        "ticket": t["ticket"],
        "pokemon": b64(make_pk7(species=25, nickname="Pikachu", ot_name="Alice")),
        "requested_species": 133,
    })
    assert status == 200

    # Bob searches
    status, found = call(server, "GET", "/gts/search?species=25")
    assert status == 200 and len(found["results"]) == 1
    data_id = found["results"][0]["data_id"]

    # Bob trades an Eevee for it
    _, prep = call(server, "POST", "/gts/prepare_trade",
                   {"user_id": BOB, "data_id": data_id})
    assert prep["pokemon"]["species"] == 25
    status, result = call(server, "POST", "/gts/trade", {
        "user_id": BOB,
        "ticket": prep["ticket"],
        "pokemon": b64(make_pk7(species=133, nickname="Eevee", ot_name="Bob")),
    })
    assert status == 200
    assert from_b64(result["pokemon"]).species == 25

    # Alice collects
    status, mine = call(server, "POST", "/gts/download_my",
                        {"data_id": dep["data_id"], "password": dep["password"]})
    assert status == 200 and mine["traded"] is True
    received = from_b64(mine["pokemon"])
    assert received.species == 133
    assert received.ot_name == "Bob"


def test_wonder_trade_over_http(server):
    status, r1 = call(server, "POST", "/wonder/enter",
                      {"user_id": ALICE, "pokemon": b64(make_pk7(species=25))})
    assert status == 200 and r1["matched"] is False

    status, r2 = call(server, "POST", "/wonder/enter",
                      {"user_id": BOB, "pokemon": b64(make_pk7(species=133))})
    assert status == 200 and r2["matched"] is True
    assert from_b64(r2["pokemon"]).species == 25

    _, r3 = call(server, "POST", "/wonder/collect",
                 {"entry_id": r1["entry_id"], "password": r1["password"]})
    assert r3["matched"] is True
    assert from_b64(r3["pokemon"]).species == 133


def test_error_mapping(server):
    status, err = call(server, "GET", "/gts/download_other?data_id=999")
    assert status == 404 and err["error"] == "not_found"

    status, err = call(server, "POST", "/gts/upload", {
        "user_id": ALICE, "ticket": 1, "pokemon": "not-base64!!",
    })
    assert status == 400

    status, err = call(server, "GET", "/nope")
    assert status == 404 and err["error"] == "no_route"


def test_illegal_upload_rejected(server):
    _, t = call(server, "POST", "/gts/prepare_upload", {"user_id": ALICE})
    bad = make_pk7()
    bad.species = 20000  # out of range
    status, err = call(server, "POST", "/gts/upload", {
        "user_id": ALICE, "ticket": t["ticket"], "pokemon": b64(bad),
        "requested_species": 1,
    })
    assert status == 400 and err["error"] == "illegal"
