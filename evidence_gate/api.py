"""Authenticated loopback HTTP demo; not a production identity gateway."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os

from .core import EvidenceGate, Principal


def load_principals(value: str | None = None) -> dict[str, Principal]:
    value = value if value is not None else os.environ.get("EVIDENCE_GATE_TOKENS", "")
    if not value:
        raise ValueError("Set EVIDENCE_GATE_TOKENS to a JSON map of bearer tokens to tenant/roles")
    raw = json.loads(value)
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Token configuration must be a nonempty JSON object")
    result = {}
    for token, context in raw.items():
        if not isinstance(token, str) or len(token) < 16 or not isinstance(context, dict):
            raise ValueError("Each token must contain at least 16 characters and map to a principal")
        roles = context.get("roles")
        tenant = context.get("tenant")
        if not isinstance(tenant, str) or not tenant or not isinstance(roles, list) or not roles or not all(isinstance(r, str) and r for r in roles):
            raise ValueError("Each principal needs a tenant and nonempty string roles")
        result[token] = Principal(tenant, tuple(roles))
    return result


def make_handler(gate: EvidenceGate, principals: dict[str, Principal]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "EvidenceGateDemo/1"

        def log_message(self, *args):
            # Avoid recording query content, credentials, or document content.
            pass

        def send_json(self, code: int, payload: dict):
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.send_json(200, {"status": "ok", "mode": "extractive_reference"})
            else:
                self.send_json(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/v1/query":
                self.send_json(404, {"error": "not_found"})
                return
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                self.send_json(401, {"error": "unauthorized"})
                return
            token = authorization[7:]
            principal = next((p for known, p in principals.items()
                              if hmac.compare_digest(known.encode("utf-8"), token.encode("utf-8"))), None)
            if principal is None:
                self.send_json(401, {"error": "unauthorized"})
                return
            if self.headers.get("Transfer-Encoding"):
                self.send_json(400, {"error": "unsupported_transfer_encoding"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192:
                    self.send_json(413, {"error": "body_must_be_1_to_8192_bytes"})
                    return
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict) or set(payload) - {"text", "region", "as_of"}:
                    raise ValueError("unexpected_fields")
                answer = gate.query(payload.get("text"), principal, region=payload.get("region"),
                                    as_of=payload.get("as_of", "2026-11-04"))
            except (ValueError, TypeError, UnicodeDecodeError):
                self.send_json(400, {"error": "invalid_request"})
                return
            self.send_json(200, answer.to_dict())

    return Handler


def serve(gate: EvidenceGate, port: int = 8080):
    principals = load_principals()
    # Bind deliberately to loopback. TLS, identity lifecycle, rate limiting,
    # distributed authorization, and production observability are out of scope.
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(gate, principals))
    print(f"Evidence Gate demo at http://127.0.0.1:{port}; Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
