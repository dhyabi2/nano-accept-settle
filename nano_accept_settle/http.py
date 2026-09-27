"""Optional tiny HTTP handler: POST /accept-and-settle {"deal":{...},"deliverable":...,"block_hash":"..."}.

    python -m nano_accept_settle.http --port 8420

The response body is the receipt; the HTTP status is the receipt's status (200/402/409/422/503),
or 400 for a malformed request. Bind to localhost unless you put it behind your own TLS proxy.
"""
import argparse
import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import Deal, DealError, Rpc, UsedHashLedger, accept_and_settle

MAX_BODY = 2_000_000


def make_handler(rpc=None, ledger=None):
    rpc = rpc or Rpc()
    ledger = ledger or UsedHashLedger()

    class Handler(BaseHTTPRequestHandler):
        server_version = "nano-accept-settle"
        sys_version = ""

        def _send(self, status, obj):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/accept-and-settle":
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n <= 0 or n > MAX_BODY:
                return self._send(400, {"error": "body required, at most %d bytes" % MAX_BODY})
            try:
                req = json.loads(self.rfile.read(n))
                deal = Deal.from_dict(req["deal"])
                if "deliverable_b64" in req:
                    deliverable = base64.b64decode(req["deliverable_b64"], validate=True)
                else:
                    deliverable = req["deliverable"]
                block_hash = req["block_hash"]
            except (ValueError, KeyError, TypeError, DealError) as e:
                return self._send(400, {"error": "bad request: %s" % e})
            receipt = accept_and_settle(deal, deliverable, block_hash, rpc=rpc, ledger=ledger)
            self._send(receipt["status"], receipt)

        def log_message(self, fmt, *args):
            pass

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8420)
    ap.add_argument("--rpc", default=None)
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)
    handler = make_handler(Rpc(a.rpc) if a.rpc else None, UsedHashLedger(a.db) if a.db else None)
    ThreadingHTTPServer((a.host, a.port), handler).serve_forever()


if __name__ == "__main__":
    main()
