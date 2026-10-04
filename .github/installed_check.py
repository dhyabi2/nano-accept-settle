"""Exercise the INSTALLED package end to end, offline, from outside the checkout.

The unit suite runs from the source tree, so it proves the source and not the wheel.
`[tool.setuptools] packages = ["nano_accept_settle"]` names the package by hand, so a new
sub-package added without being listed there imports fine in a checkout and is simply absent
from the wheel. This script is therefore run from a directory that is NOT the checkout, so the
checkout's own `nano_accept_settle/` cannot satisfy the import and hide a packaging gap.

Offline: the only socket is a loopback stub that answers `block_info`. Nothing reaches the
public ledger, so this is safe to run on a runner with no network.

Usage: python3 installed_check.py
Exits non-zero on the first failed assertion.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Imported from the install, never from a checkout: see the module docstring.
import nano_accept_settle
from nano_accept_settle import RAW_PER_XNO, Deal, UsedHashLedger, accept_and_settle, check_acceptance
from nano_accept_settle.http import main as http_main, make_handler
from nano_accept_settle.nano import address_to_pubkey, pubkey_to_address, state_block_hash

PKG_DIR = os.path.dirname(os.path.abspath(nano_accept_settle.__file__))
REP = pubkey_to_address(hashlib.sha256(b"rep").digest())
ASKER = pubkey_to_address(hashlib.sha256(b"asker").digest())
ANSWERER = pubkey_to_address(hashlib.sha256(b"answerer").digest())
PRICE_XNO = "0.01"
PRICE_RAW = str(RAW_PER_XNO // 100)  # 0.01 XNO as an exact integer of raw


def check(label, got, want):
    if got != want:
        sys.exit("FAIL %s\n  got  %r\n  want %r" % (label, got, want))
    print("  ok  %s = %r" % (label, got))


class Blocks(dict):
    """Hash-consistent state blocks, hashed by the installed package's own blake2b."""

    def add(self, account, previous, balance, link_hex, confirmed=True):
        c = {"type": "state", "account": account, "previous": previous, "representative": REP,
             "balance": str(balance), "link": link_hex}
        h = state_block_hash(c)
        self[h] = {"contents": c, "confirmed": "true" if confirmed else "false",
                   "local_timestamp": "1790000000", "amount": None, "subtype": None}
        return h

    def chain_send(self, sender, dest, amount_raw, start_balance=10 * RAW_PER_XNO):
        opened = self.add(sender, "0" * 64, start_balance, "AB" * 32)
        return self.add(sender, opened, start_balance - int(amount_raw),
                        address_to_pubkey(dest).hex().upper())


def serve_rpc(blocks):
    """A loopback node that answers only block_info, from blocks it was handed."""

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert req["action"] == "block_info", req
            b = blocks.get(req["hash"].upper())
            body = json.dumps(dict(b) if b else {"error": "Block not found"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main():
    print("nano_accept_settle %s installed at %s" % (nano_accept_settle.__version__, PKG_DIR))
    if os.path.abspath(os.getcwd()) == os.path.dirname(PKG_DIR):
        sys.exit("FAIL this script must not run from the checkout: the import would come from the tree")

    # 1. Every module the package ships imports from the install.
    shipped = sorted(f[:-3] for f in os.listdir(PKG_DIR) if f.endswith(".py") and f != "__init__.py")
    print("shipped modules: %s" % ", ".join(shipped))
    if not shipped:
        sys.exit("FAIL the installed package ships no modules - the wheel is empty")
    for m in shipped:
        __import__("nano_accept_settle." + m)
    check("modules imported from the install", shipped, ["core", "http", "nano"])

    # 2. The money unit is an exact integer of raw, in the installed code.
    check("0.01 XNO in raw", PRICE_RAW, "10000000000000000000000000000")
    check("RAW_PER_XNO is an int", (RAW_PER_XNO, type(RAW_PER_XNO) is int), (10 ** 30, True))

    blocks = Blocks()
    block_hash = blocks.chain_send(ASKER, ANSWERER, PRICE_RAW)
    srv, rpc_url = serve_rpc(blocks)
    deal = {"asker": ASKER, "answerer": ANSWERER, "amount_raw": PRICE_RAW,
            "acceptance": {"type": "regex", "pattern": r"^\d+$", "fullmatch": True},
            "deadline": "2026-12-31T00:00:00Z", "what_it_buys": "the answer to a number question"}

    # 3. The library call, in process, against the loopback node.
    with tempfile.TemporaryDirectory() as tmp:
        receipt = accept_and_settle(Deal.from_dict(deal), "42", block_hash,
                                    rpc=nano_accept_settle.Rpc(rpc_url),
                                    ledger=UsedHashLedger(os.path.join(tmp, "lib.sqlite3")))
        check("library status", receipt["status"], 200)
        check("library paid", receipt["paid"], True)
        check("library accepted the deliverable", receipt["accepted"], True)
        check("library amount is the exact raw asked for",
              receipt["payment"]["amount_raw"], PRICE_RAW)
        check("library reasons are empty on a clean settle", receipt["reasons"], [])

    # 4. README's documented server command, run as a subprocess of the install.
    with tempfile.TemporaryDirectory() as tmp:
        port = 18420
        proc = subprocess.Popen(
            [sys.executable, "-m", "nano_accept_settle.http", "--host", "127.0.0.1",
             "--port", str(port), "--rpc", rpc_url, "--db", os.path.join(tmp, "http.sqlite3")],
            cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        url = "http://127.0.0.1:%d/accept-and-settle" % port
        body = {"deal": deal, "deliverable": "42", "block_hash": block_hash}
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    sys.exit("FAIL the documented server exited: %s" % proc.stdout.read().decode())
                try:
                    status, receipt = post(url, body)
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                sys.exit("FAIL the documented server never accepted a connection")

            check("POST /accept-and-settle status", status, 200)
            check("http paid", receipt["paid"], True)
            check("http amount", receipt["payment"]["amount_raw"], PRICE_RAW)
            check("http sender", receipt["payment"]["account"], ASKER)
            check("http destination", receipt["payment"]["destination"], ANSWERER)

            # The used-hash ledger holds across requests in the installed process, and it
            # distinguishes a retry from a reuse. Replaying the SAME deal is idempotent (the
            # ledger's owner is this deal already), so an asker who loses the response and asks
            # again is not told their payment is gone.
            again, replay = post(url, body)
            check("the same deal replayed is idempotent", (again, replay["paid"]), (200, True))

            # A DIFFERENT deal presenting the same block is the reuse the ledger exists to
            # refuse: one payment settles one deal. 409 is the documented status for that.
            other = dict(deal, what_it_buys="a different task")
            reused_status, reused = post(url, dict(body, deal=other))
            check("another deal reusing the block", (reused_status, reused["paid"]), (409, False))
            check("...and the reason names it",
                  "block already settled another deal" in reused["reasons"], True)

            bad, refused = post(url, dict(body, deliverable="forty-two"))
            check("a deliverable that fails the acceptance test", bad, 422)
            # accept_and_settle returns at 422 before verify_payment is reached, so the receipt
            # carries no payment key at all: that absence IS the proof the node was not consulted.
            check("...and the node is not consulted for it", "payment" in refused, False)
        finally:
            proc.terminate()
            proc.wait(timeout=30)
    srv.shutdown()

    # 5. The acceptance check is pure and needs no node at all.
    passed = check_acceptance(Deal.from_dict(deal), "42")["passed"]
    check("check_acceptance offline", passed, True)
    print("\nALL INSTALLED CHECKS PASSED")


if __name__ == "__main__":
    main()
