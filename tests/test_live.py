"""One read-only live check against the public ledger. Skipped when NANO_ACCEPT_SETTLE_OFFLINE=1."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nano_accept_settle import Deal, Rpc, UsedHashLedger, read_send, verify_payment  # noqa: E402

BLOCK = "B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F"
SENDER = "nano_3m8cz87zwxb1y16ob4bzp1eyek78qaig8ktohk7d45b18sh6u9exbowbnekr"
DEST = "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7"
AMOUNT = "500000000000000000000000000"  # 0.0005 XNO


@unittest.skipIf(os.environ.get("NANO_ACCEPT_SETTLE_OFFLINE") == "1", "offline")
class Live(unittest.TestCase):
    def test_reads_a_real_historical_send(self):
        rpc = Rpc(os.environ.get("NANO_RPC", "https://rpc.nano.to"))
        b = read_send(BLOCK, rpc)
        self.assertEqual((b["account"], b["destination"], b["amount_raw"], b["is_send"], b["confirmed"]),
                         (SENDER, DEST, AMOUNT, True, True))
        self.assertEqual(b["rpc_amount_raw"], AMOUNT, "our balance-delta amount agrees with the node's")
        deal = Deal(SENDER, DEST, AMOUNT, {"type": "regex", "pattern": "."})
        self.assertTrue(verify_payment(deal, BLOCK, rpc=rpc, ledger=UsedHashLedger(":memory:"))["paid"])
        self.assertFalse(verify_payment(Deal(SENDER, DEST, "1", {"type": "regex", "pattern": "."}),
                                        BLOCK, rpc=rpc)["paid"])


if __name__ == "__main__":
    unittest.main()
