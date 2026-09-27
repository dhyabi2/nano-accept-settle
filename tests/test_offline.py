"""Offline laws: a mocked RPC that serves hash-consistent state blocks."""
import hashlib
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nano_accept_settle import (  # noqa: E402
    RAW_PER_XNO, Deal, DealError, UsedHashLedger, accept_and_settle, check_acceptance, verify_payment,
)
from nano_accept_settle.nano import (  # noqa: E402
    AddressError, address_to_pubkey, pubkey_to_address, state_block_hash,
)


def addr(seed):
    return pubkey_to_address(hashlib.sha256(seed.encode()).digest())


ASKER, ANSWERER, OTHER, REP = addr("asker"), addr("answerer"), addr("other"), addr("rep")
PRICE = str(RAW_PER_XNO // 100)  # 0.01 XNO


class MockRpc:
    """Serves block_info for blocks it built itself; every hash is the real state-block hash."""

    def __init__(self):
        self.blocks = {}
        self.calls = 0

    def add(self, account, previous, balance, link_hex, confirmed=True):
        c = {"type": "state", "account": account, "previous": previous, "representative": REP,
             "balance": str(balance), "link": link_hex}
        h = state_block_hash(c)
        self.blocks[h] = {"contents": c, "confirmed": "true" if confirmed else "false", "local_timestamp": "1790000000"}
        return h

    def chain_send(self, sender, dest, amount, start_balance=10 * RAW_PER_XNO, confirmed=True):
        opened = self.add(sender, "0" * 64, start_balance, "AB" * 32)
        return self.add(sender, opened, start_balance - int(amount), address_to_pubkey(dest).hex().upper(), confirmed)

    def __call__(self, payload):
        self.calls += 1
        assert payload["action"] == "block_info"
        b = self.blocks.get(payload["hash"].upper())
        return dict(b) if b else {"error": "Block not found"}


def deal(**kw):
    d = dict(asker=ASKER, answerer=ANSWERER, amount_raw=PRICE,
             acceptance={"type": "regex", "pattern": r"^\d+$", "fullmatch": True},
             deadline="2026-12-31T00:00:00Z", what_it_buys="the answer to a number question")
    d.update(kw)
    return Deal.from_dict(d)


class Acceptance(unittest.TestCase):
    def test_sha256(self):
        d = deal(acceptance={"type": "sha256", "sha256": hashlib.sha256(b"hello").hexdigest()})
        self.assertTrue(check_acceptance(d, b"hello")["passed"])
        self.assertFalse(check_acceptance(d, b"hellO")["passed"])

    def test_required_keys_and_types(self):
        d = deal(acceptance={"type": "required_keys", "required": ["price", "unit"], "types": {"price": "number"}})
        self.assertTrue(check_acceptance(d, '{"price": 3.2, "unit": "XNO"}')["passed"])
        self.assertTrue(check_acceptance(d, {"price": 3, "unit": "XNO"})["passed"])
        r = check_acceptance(d, '{"price": "3", "note": 1}')
        self.assertFalse(r["passed"])
        self.assertIn("missing key: unit", r["reasons"])
        self.assertIn("key price is not number", r["reasons"])
        self.assertFalse(check_acceptance(d, '{"price": true, "unit": "x"}')["passed"])
        self.assertFalse(check_acceptance(d, "not json")["passed"])

    def test_regex(self):
        d = deal()
        self.assertTrue(check_acceptance(d, "42")["passed"])
        self.assertFalse(check_acceptance(d, "42 apples")["passed"])


class DealSchema(unittest.TestCase):
    def test_rejects_floats_bad_addresses_and_unknown_fields(self):
        for bad in [dict(amount_raw=0.01), dict(amount_raw="1.5"), dict(amount_raw="0"), dict(amount_raw="-1"),
                    dict(asker="nano_1111"), dict(answerer=ASKER[:-1] + ("1" if ASKER[-1] != "1" else "3")),
                    dict(answerer=ASKER), dict(acceptance={"type": "eval"}), dict(surprise=1),
                    dict(deadline="2026-12-31T00:00:00")]:
            with self.assertRaises(DealError, msg=bad):
                deal(**bad)

    def test_deal_id_is_stable_and_checked(self):
        d = deal()
        self.assertEqual(d.deal_id, deal().deal_id)
        self.assertNotEqual(d.deal_id, deal(amount_raw=str(int(PRICE) + 1)).deal_id)
        with self.assertRaises(DealError):
            Deal.from_dict(dict(deal().to_dict(), deal_id="0" * 64))

    def test_terms_amount_is_exact(self):
        self.assertEqual(deal().terms()["amount_xno"], "0.01")
        self.assertEqual(deal(amount_raw="1").terms()["amount_xno"], "0." + "0" * 29 + "1")
        self.assertEqual(deal(amount_raw=str(3 * RAW_PER_XNO)).terms()["amount_xno"], "3")


class Payment(unittest.TestCase):
    def setUp(self):
        self.rpc = MockRpc()
        self.ledger = UsedHashLedger(":memory:")

    def settle(self, h, d=None, deliverable="42"):
        return accept_and_settle(d or deal(), deliverable, h, rpc=self.rpc, ledger=self.ledger)

    def test_happy_path(self):
        h = self.rpc.chain_send(ASKER, ANSWERER, PRICE)
        r = self.settle(h)
        self.assertEqual((r["status"], r["accepted"], r["paid"], r["reasons"]), (200, True, True, []))
        self.assertEqual(r["payment"]["amount_raw"], PRICE)
        self.assertTrue(r["payment"]["on_time"])
        self.assertEqual(self.settle(h)["status"], 200, "same block, same deal is idempotent")

    def test_wrong_destination(self):
        r = self.settle(self.rpc.chain_send(ASKER, OTHER, PRICE))
        self.assertEqual(r["status"], 402)
        self.assertTrue(any("not the answerer" in x for x in r["reasons"]))
        self.assertEqual(r["terms"]["pay_to"], ANSWERER)
        self.assertEqual(r["terms"]["amount_raw"], PRICE)

    def test_wrong_sender(self):
        r = self.settle(self.rpc.chain_send(OTHER, ANSWERER, PRICE))
        self.assertEqual(r["status"], 402)
        self.assertTrue(any("not the asker" in x for x in r["reasons"]))

    def test_wrong_amount_both_ways(self):
        for amt in (int(PRICE) - 1, int(PRICE) + 1):
            r = self.settle(self.rpc.chain_send(ASKER, ANSWERER, str(amt)))
            self.assertEqual(r["status"], 402, amt)
            self.assertTrue(any("deal says" in x for x in r["reasons"]))

    def test_unconfirmed(self):
        h = self.rpc.chain_send(ASKER, ANSWERER, PRICE, confirmed=False)
        r = self.settle(h)
        self.assertEqual(r["status"], 402)
        self.assertIn("block is not confirmed yet", r["reasons"])
        self.assertIsNone(self.ledger.owner(h), "an unpaid block is never recorded")

    def test_receive_block_is_not_a_payment(self):
        opened = self.rpc.add(ASKER, "0" * 64, int(PRICE), "CD" * 32)
        h = self.rpc.add(ASKER, opened, 2 * int(PRICE), "EF" * 32)
        r = self.settle(h)
        self.assertEqual(r["status"], 402)
        self.assertIn("block is not a send", r["reasons"])

    def test_reused_hash(self):
        h = self.rpc.chain_send(ASKER, ANSWERER, PRICE)
        self.assertEqual(self.settle(h)["status"], 200)
        other = deal(what_it_buys="a different task")
        r = self.settle(h, other)
        self.assertEqual((r["status"], r["paid"]), (409, False))
        self.assertIn("block already settled another deal", r["reasons"])

    def test_acceptance_failure_is_422_and_skips_rpc(self):
        h = self.rpc.chain_send(ASKER, ANSWERER, PRICE)
        r = self.settle(h, deliverable="forty-two")
        self.assertEqual((r["status"], r["accepted"], r["paid"]), (422, False, False))
        self.assertEqual(self.rpc.calls, 0)
        self.assertIsNone(self.ledger.owner(h))

    def test_rpc_that_lies_about_contents_is_refused(self):
        h = self.rpc.chain_send(ASKER, OTHER, PRICE)
        forged = self.rpc.chain_send(ASKER, ANSWERER, PRICE)
        self.rpc.blocks[h] = self.rpc.blocks[forged]  # serve a valid-looking block under the wrong hash
        r = self.settle(h)
        self.assertEqual(r["status"], 503)
        self.assertTrue(any("do not hash" in x for x in r["reasons"]))

    def test_unknown_block_and_bad_hash(self):
        self.assertEqual(self.settle("A" * 64)["status"], 503)
        self.assertEqual(self.settle("xyz")["status"], 402)

    def test_verify_payment_does_not_record(self):
        h = self.rpc.chain_send(ASKER, ANSWERER, PRICE)
        self.assertTrue(verify_payment(deal(), h, rpc=self.rpc, ledger=self.ledger)["paid"])
        self.assertIsNone(self.ledger.owner(h))

    def test_late_payment_is_flagged(self):
        h = self.rpc.chain_send(ASKER, ANSWERER, PRICE)
        r = self.settle(h, deal(deadline=1700000000))
        self.assertEqual(r["status"], 200)
        self.assertFalse(r["payment"]["on_time"])

    def test_ledger_persists_across_instances(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "used.db")
            h = self.rpc.chain_send(ASKER, ANSWERER, PRICE)
            self.assertEqual(accept_and_settle(deal(), "42", h, rpc=self.rpc, ledger=UsedHashLedger(path))["status"], 200)
            r = accept_and_settle(deal(what_it_buys="x"), "42", h, rpc=self.rpc, ledger=UsedHashLedger(path))
            self.assertEqual(r["status"], 409)


class Addresses(unittest.TestCase):
    def test_roundtrip_and_checksum(self):
        pub = hashlib.sha256(b"k").digest()
        a = pubkey_to_address(pub)
        self.assertEqual(address_to_pubkey(a), pub)
        self.assertEqual(address_to_pubkey("xrb_" + a[5:]), pub)
        with self.assertRaises(AddressError):
            address_to_pubkey(a[:-1] + ("3" if a[-1] == "1" else "1"))

    def test_known_address(self):
        # link of the live block B749...A58F and the address the network reports for it
        self.assertEqual(pubkey_to_address(bytes.fromhex(
            "7AA450344121ED8AF805F61F9A534B2E69C2A5C7F0F67A5C3B2E4CDA710FB902")),
            "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7")


class Http(unittest.TestCase):
    def test_status_codes(self):
        from http.server import ThreadingHTTPServer
        from nano_accept_settle.http import make_handler
        rpc = MockRpc()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(rpc, UsedHashLedger(":memory:")))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d/accept-and-settle" % srv.server_address[1]

        def post(body):
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        try:
            d = deal().to_dict()
            good = rpc.chain_send(ASKER, ANSWERER, PRICE)
            self.assertEqual(post({"deal": d, "deliverable": "42", "block_hash": good})[0], 200)
            code, body = post({"deal": d, "deliverable": "42", "block_hash": rpc.chain_send(ASKER, OTHER, PRICE)})
            self.assertEqual((code, body["terms"]["pay_to"]), (402, ANSWERER))
            self.assertEqual(post({"deal": dict(d, what_it_buys="other"), "deliverable": "42", "block_hash": good})[0], 409)
            self.assertEqual(post({"deal": d, "deliverable": "nope", "block_hash": good})[0], 422)
            self.assertEqual(post({"deal": dict(d, amount_raw=1.0), "deliverable": "42", "block_hash": good})[0], 400)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
