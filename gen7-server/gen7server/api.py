"""JSON/HTTP development API over the GTS, Wonder Trade and Mystery Gift backends.

A real 3DS does not speak this protocol — it speaks NEX (PRUDP) DataStore.
This façade exists so the game logic can be exercised, tested, and demoed
end-to-end today, and so save-editing tools (PKSM, PKHeX scripts) could use
the server directly. The endpoint set maps 1:1 onto the DataStore custom
methods (see gts.py), which is exactly the seam where a NEX transport
(e.g. Pretendo's nex-go) gets attached later.

Binary Pokémon payloads travel as base64 of the encrypted 232-byte PK7.
Stdlib-only on purpose: no dependencies to install, runs anywhere.
"""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .gts import GTS, GTSError, RecordKey
from .mystery_gift import MysteryGift, MysteryGiftError
from .wonder_trade import WonderTrade, WonderTradeError


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def _unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        raise ApiError(400, "bad_base64", "invalid base64 payload")


class Gen7Server:
    """Bundles the three services plus the HTTP plumbing."""

    def __init__(self, db_path: str = ":memory:", *, require_legal: bool = True):
        # ":memory:" gives each service its own private db; a file path is shared.
        self.gts = GTS(db_path, require_legal=require_legal)
        self.wonder = WonderTrade(db_path, require_legal=require_legal)
        self.gifts = MysteryGift(db_path)
        self._httpd: ThreadingHTTPServer | None = None
        # SQLite connections are shared across handler threads; serialize access.
        self._lock = threading.Lock()

    # -- request dispatch ------------------------------------------------

    def handle(self, method: str, path: str, query: dict, body: dict) -> dict:
        try:
            with self._lock:
                return self._route(method, path, query, body)
        except (GTSError, WonderTradeError, MysteryGiftError) as e:
            status = {
                "not_found": 404, "gone": 410, "denied": 403,
                "bad_ticket": 403, "used": 409, "own_record": 409,
                "mismatch": 409, "not_live": 410,
            }.get(e.code, 400)
            raise ApiError(status, e.code, str(e))

    def _route(self, method: str, path: str, query: dict, body: dict) -> dict:
        def q_int(name, default=None):
            if name in query:
                return int(query[name][0])
            return default

        # ---- GTS (DataStore methods 40-47) ----
        if (method, path) == ("POST", "/gts/prepare_upload"):
            return {"ticket": self.gts.prepare_upload(int(body["user_id"]))}

        if (method, path) == ("POST", "/gts/upload"):
            key = self.gts.upload(
                int(body["user_id"]),
                int(body["ticket"]),
                _unb64(body["pokemon"]),
                requested_species=int(body.get("requested_species", 0)),
                requested_gender=int(body.get("requested_gender", 2)),
                requested_min_level=int(body.get("requested_min_level", 1)),
                requested_max_level=int(body.get("requested_max_level", 100)),
                message=str(body.get("message", ""))[:64],
            )
            return {"data_id": key.data_id, "password": key.password}

        if (method, path) == ("GET", "/gts/search"):
            return {
                "results": self.gts.search(
                    species=q_int("species"),
                    gender=q_int("gender"),
                    min_level=q_int("min_level", 1),
                    max_level=q_int("max_level", 100),
                    shiny_only=q_int("shiny_only", 0) == 1,
                    exclude_owner=q_int("exclude_owner"),
                    offset=q_int("offset", 0),
                    limit=q_int("limit", 20),
                )
            }

        if (method, path) == ("POST", "/gts/prepare_trade"):
            ticket, summary = self.gts.prepare_trade(
                int(body["user_id"]), int(body["data_id"])
            )
            return {"ticket": ticket, "pokemon": summary}

        if (method, path) == ("POST", "/gts/trade"):
            received = self.gts.trade(
                int(body["user_id"]), int(body["ticket"]), _unb64(body["pokemon"])
            )
            return {"pokemon": _b64(received)}

        if (method, path) == ("GET", "/gts/download_other"):
            return {"pokemon": _b64(self.gts.download_other(q_int("data_id")))}

        if (method, path) == ("POST", "/gts/download_my"):
            result = self.gts.download_my(
                RecordKey(int(body["data_id"]), int(body["password"]))
            )
            if result.get("pokemon") is not None:
                result["pokemon"] = _b64(result["pokemon"])
            return result

        if (method, path) == ("POST", "/gts/delete"):
            blob = self.gts.delete(
                RecordKey(int(body["data_id"]), int(body["password"]))
            )
            return {"pokemon": _b64(blob)}

        # ---- Wonder Trade ----
        if (method, path) == ("POST", "/wonder/enter"):
            result = self.wonder.enter(int(body["user_id"]), _unb64(body["pokemon"]))
            if result.get("pokemon") is not None:
                result["pokemon"] = _b64(result["pokemon"])
            return result

        if (method, path) == ("POST", "/wonder/collect"):
            result = self.wonder.collect(int(body["entry_id"]), int(body["password"]))
            if result.get("pokemon") is not None:
                result["pokemon"] = _b64(result["pokemon"])
            return result

        if (method, path) == ("POST", "/wonder/cancel"):
            blob = self.wonder.cancel(int(body["entry_id"]), int(body["password"]))
            return {"pokemon": _b64(blob)}

        if (method, path) == ("GET", "/wonder/pool"):
            return {"waiting": self.wonder.pool_size()}

        # ---- Mystery Gift ----
        if (method, path) == ("GET", "/gift/list"):
            return {"gifts": self.gifts.list_live()}

        if (method, path) == ("POST", "/gift/download"):
            card = self.gifts.download(int(body["gift_id"]), int(body["user_id"]))
            return {"card": _b64(card)}

        if (method, path) == ("POST", "/gift/redeem"):
            card = self.gifts.redeem_code(str(body["code"]), int(body["user_id"]))
            return {"card": _b64(card)}

        if (method, path) == ("GET", "/health"):
            return {"ok": True, "waiting_wonder_trades": self.wonder.pool_size()}

        raise ApiError(404, "no_route", f"no handler for {method} {path}")

    # -- HTTP plumbing ---------------------------------------------------

    def serve(self, host: str = "127.0.0.1", port: int = 8272) -> ThreadingHTTPServer:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _respond(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _dispatch(self, method: str) -> None:
                parsed = urlparse(self.path)
                body = {}
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    try:
                        body = json.loads(self.rfile.read(length))
                    except json.JSONDecodeError:
                        self._respond(400, {"error": "bad_json", "message": "body is not valid JSON"})
                        return
                try:
                    result = server.handle(method, parsed.path, parse_qs(parsed.query), body)
                    self._respond(200, result)
                except ApiError as e:
                    self._respond(e.status, {"error": e.code, "message": str(e)})
                except (KeyError, ValueError, TypeError) as e:
                    self._respond(400, {"error": "bad_request", "message": str(e)})

            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

        httpd = ThreadingHTTPServer((host, port), Handler)
        self._httpd = httpd
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd

    def shutdown(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None


def main() -> None:  # pragma: no cover - manual entry point
    import argparse

    parser = argparse.ArgumentParser(description="Gen 7 revival server (dev API)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8272)
    parser.add_argument("--db", default="gen7server.sqlite3")
    args = parser.parse_args()

    server = Gen7Server(args.db)
    server.serve(args.host, args.port)
    print(f"gen7server dev API listening on http://{args.host}:{args.port}")
    threading.Event().wait()


if __name__ == "__main__":  # pragma: no cover
    main()
