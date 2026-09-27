# nano-accept-settle

One call that answers the question at the end of every paid task: **did the answer pass its test, and has the
asker's XNO payment settled?** If both are true the task is done and paid, at machine speed.

**Non-custodial.** This library only *reads* the public Nano ledger. It never holds, signs or moves funds, never
sees a seed or a private key, and contains no code that could.

Python 3.8+, standard library only. Maintained by the team behind [getunstuck.space](https://getunstuck.space).

## Quickstart

```bash
python3 -m venv v && . v/bin/activate
pip install git+https://github.com/dhyabi2/nano-accept-settle
python3 - <<'PY'
from nano_accept_settle import Deal, accept_and_settle
deal = Deal(asker="nano_3m8cz87zwxb1y16ob4bzp1eyek78qaig8ktohk7d45b18sh6u9exbowbnekr",
            answerer="nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7",
            amount_raw="500000000000000000000000000", acceptance={"type": "regex", "pattern": r"^\d+$"})
r = accept_and_settle(deal, "42", "B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F")
print(r["status"], r["accepted"], r["paid"], r["reasons"])   # 200 True True []
PY
```

That block is a real, confirmed 0.0005 XNO send on mainnet, so the example settles for real (read-only).
Run it twice with a different `what_it_buys` and you get 409: one payment settles one deal.

## API

- `Deal(asker, answerer, amount_raw, acceptance, deadline=None, what_it_buys=None)` / `Deal.from_dict(d)` —
  the terms. Schema pinned in [SCHEMA.md](SCHEMA.md). Amounts are integer strings of raw; floats are refused.
- `check_acceptance(deal, deliverable) -> {"passed", "reasons"}` — sha256, required JSON keys, or regex.
- `verify_payment(deal, block_hash, rpc=None, ledger=None)` — reads `block_info` (default `https://rpc.nano.to`,
  with a User-Agent) and requires a **confirmed send from the asker to the answerer of exactly `amount_raw`**
  that has not settled another deal. It does not take the node's word for the block: the state-block hash is
  recomputed from the returned fields, and the amount is the balance drop from the (also hash-checked) previous
  block. The node's `amount` must agree in the live test.
- `accept_and_settle(deal, deliverable, block_hash, rpc=None, ledger=None)` — both, in one receipt with an HTTP
  status: **200** accepted and paid; **402** accepted but unpaid, with the deal terms and `pay_to`; **409** the
  block already settled another deal; **422** the acceptance test failed; **503** RPC unreadable, retry.
- `UsedHashLedger(path)` — local SQLite of block -> deal (default `~/.nano-accept-settle/used.db`, or
  `NANO_ACCEPT_SETTLE_DB`). A block is recorded only on a 200, atomically.
- Optional HTTP: `python -m nano_accept_settle.http --port 8420` serves `POST /accept-and-settle` with
  `{"deal": {...}, "deliverable": "..." | "deliverable_b64": "...", "block_hash": "..."}`; the response status is
  the receipt's status (400 for a malformed request). Binds to 127.0.0.1 by default.

## Limits (read these)

- **Nano sends are irreversible, and this library cannot hold funds in escrow.** Nothing here can refund, freeze
  or claw back a payment. The recommended pattern is: the asker runs `check_acceptance` on the deliverable
  **first**, pays only if it passed, then calls `accept_and_settle` with the block hash so both sides have a
  receipt. For recourse beyond that (a bad answer that passed a weak test, a dispute about what was agreed),
  use a human-agreed third party. This project contains no custodial code and will not add any.
- The acceptance test is only as good as its spec. A regex or required-keys check proves shape, not truth.
- **Confirmation is taken from the RPC node.** Block contents are hash-checked locally, but signatures are not
  verified here (that needs ed25519-blake2b, not in the standard library) and `confirmed` is the node's answer.
  For high value, point `Rpc(url)` at a node you run, or query two independent nodes and require both.
- The used-hash ledger is local. Two verifiers with separate ledgers could each accept the same block for
  different deals; share one ledger (one database file, or one HTTP service) per marketplace.
- `deadline` is informational (`on_time` uses the node's `local_timestamp`, which some nodes report as 0 for old
  blocks, giving `on_time: null`).

## Tests

```bash
python3 -m unittest discover -s tests -v          # offline laws + one read-only live check
NANO_ACCEPT_SETTLE_OFFLINE=1 python3 -m unittest discover -s tests   # offline only
```

MIT licensed.
