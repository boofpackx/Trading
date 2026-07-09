"""End-to-end demo: two simulated players use the GTS and Wonder Trade over HTTP.

Run with:  python3 -m gen7server.demo
"""

from __future__ import annotations

import base64
import json
import urllib.request

from .api import Gen7Server
from .factory import make_pk7
from .pk7 import PK7

ALICE, BOB = 1, 2


def call(base: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def b64(pk: PK7) -> str:
    return base64.b64encode(pk.to_encrypted()).decode()


def show(label: str, blob_b64: str) -> None:
    pk = PK7.from_encrypted(base64.b64decode(blob_b64))
    s = pk.summary()
    shiny = " (SHINY!)" if s["is_shiny"] else ""
    print(f"  {label}: #{s['species']} {s['nickname']}{shiny} "
          f"lv{s['met_level']} OT:{s['ot_name']} IVs:{s['ivs']}")


def main() -> None:
    server = Gen7Server(":memory:")
    httpd = server.serve(port=0)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"server up at {base}\n")

    print("=== GTS ===")
    print("Alice deposits a Pikachu, asking for an Eevee (lv 1-100)")
    t = call(base, "POST", "/gts/prepare_upload", {"user_id": ALICE})
    dep = call(base, "POST", "/gts/upload", {
        "user_id": ALICE, "ticket": t["ticket"],
        "pokemon": b64(make_pk7(species=25, nickname="Pikachu", ot_name="Alice", level=42)),
        "requested_species": 133, "message": "Eevee please!",
    })

    found = call(base, "GET", "/gts/search?species=25")
    r = found["results"][0]
    print(f"Bob searches for Pikachu -> found deposit {r['data_id']}: "
          f"lv{r['level']} from {r['ot_name']}, wants species #{r['req_species']}")

    prep = call(base, "POST", "/gts/prepare_trade", {"user_id": BOB, "data_id": r["data_id"]})
    result = call(base, "POST", "/gts/trade", {
        "user_id": BOB, "ticket": prep["ticket"],
        "pokemon": b64(make_pk7(species=133, nickname="Eevee", ot_name="Bob", level=30, shiny=True)),
    })
    show("Bob receives", result["pokemon"])

    mine = call(base, "POST", "/gts/download_my",
                {"data_id": dep["data_id"], "password": dep["password"]})
    show("Alice collects", mine["pokemon"])

    print("\n=== Wonder Trade ===")
    r1 = call(base, "POST", "/wonder/enter",
              {"user_id": ALICE, "pokemon": b64(make_pk7(species=778, nickname="Mimikyu", ot_name="Alice"))})
    print(f"Alice enters a Mimikyu -> waiting (pool: "
          f"{call(base, 'GET', '/wonder/pool')['waiting']})")
    r2 = call(base, "POST", "/wonder/enter",
              {"user_id": BOB, "pokemon": b64(make_pk7(species=129, nickname="Magikarp", ot_name="Bob"))})
    show("Bob enters a Magikarp and instantly receives", r2["pokemon"])
    r3 = call(base, "POST", "/wonder/collect",
              {"entry_id": r1["entry_id"], "password": r1["password"]})
    show("Alice collects", r3["pokemon"])

    print("\n=== Mystery Gift ===")
    gid = server.gifts.add_gift("Shiny Tapu Koko", b"\x07" * 0x310)
    marsh = server.gifts.add_gift("Marshadow", b"\x08" * 0x310, via_internet=False)
    server.gifts.add_serial_code("MARSHADOW20", marsh, max_uses=100)
    live = call(base, "GET", "/gift/list")
    print(f"Live internet gifts: {[g['title'] for g in live['gifts']]}")
    card = call(base, "POST", "/gift/download", {"gift_id": gid, "user_id": ALICE})
    print(f"Alice downloads 'Shiny Tapu Koko' wondercard ({len(base64.b64decode(card['card']))} bytes)")
    card = call(base, "POST", "/gift/redeem", {"code": "MARSHADOW20", "user_id": BOB})
    print(f"Bob redeems code MARSHADOW20 -> wondercard ({len(base64.b64decode(card['card']))} bytes)")

    server.shutdown()
    print("\ndemo complete")


if __name__ == "__main__":
    main()
