"""
http_app.py
-----------
Optional, dependency-free HTTP wrapper around `evaluate()` for the
"or HTTP endpoint" half of the requirement. Uses only the stdlib
(http.server) so this project has zero third-party dependencies.

Run: python -m policy_gate.http_app [port]
POST a JSON request body to /evaluate, get a JSON decision back.

A same-payload retry to the same request_id returns 200 with the original
decision. A different-payload retry to the same request_id returns 409
with an explanation (see store.py / README for why this is a conflict,
not a verdict).
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .evaluator import evaluate
from .store import IdempotencyConflictError

_STORE = None  # created lazily per-process in main()


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (stdlib naming convention)
        if self.path != "/evaluate":
            self._send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            request = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"invalid JSON body: {exc}"})
            return

        try:
            decision = evaluate(request, store=_STORE)
        except IdempotencyConflictError as exc:
            self._send_json(409, {"error": str(exc), "request_id": exc.request_id})
            return

        self._send_json(200, decision)

    def log_message(self, fmt, *args):  # quiet by default
        pass


def main() -> None:
    global _STORE
    from .store import DecisionStore

    _STORE = DecisionStore()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"policy_gate listening on :{port} (POST /evaluate)")
    server.serve_forever()


if __name__ == "__main__":
    main()
