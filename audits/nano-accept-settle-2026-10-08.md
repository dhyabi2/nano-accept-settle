# nano-accept-settle — audit 2026-10-08

Previous audit 2026-10-05. One question: can an agent get paid in XNO with this,
today, without being hurt? Audited at `969b136` (`main`). Python 3.11.

## Checked

- `python -m compileall -q nano_accept_settle tests` clean;
  `NANO_ACCEPT_SETTLE_OFFLINE=1 python -m unittest discover -s tests`:
  **32 tests, OK (skipped=1)** on `main`, **35** after this branch. The one skip
  is the live ledger test, and CI's `grep -qE 'OK \(skipped=1\)'` law still
  matches.
- **The README quickstart, run exactly as written, from a clean venv against the
  real mainnet ledger:**

  ```
  $ python3 -m venv v && . v/bin/activate
  $ pip install git+https://github.com/dhyabi2/nano-accept-settle
  $ python3 - <<'PY'   # the README's own snippet, unmodified
  200 True True []
  ```

  The documented output is `200 True True []` and that is what came back. The
  install path, the package name, the import, the three argument names and the
  pinned block hash are all correct, and `rpc.nano.to` answered. The first step
  a new agent would take works.
- `nano.py` end to end: the base32 alphabet, `address_to_pubkey`'s checksum
  (5-byte blake2b, byte-reversed), `pubkey_to_address`, and `state_block_hash` —
  preamble 6, account pubkey, `previous`, representative pubkey, balance as 16
  bytes big-endian, `link`. That is Nano's state-block preimage, which is what
  lets `read_send` refuse a node that swaps blocks.
- `core.py`'s money path: `read_send`'s independent derivation (hash recomputed,
  amount as the balance drop, destination decoded from `link`), the five checks
  in `verify_payment`, `UsedHashLedger.claim`'s `BEGIN IMMEDIATE`, and
  `accept_and_settle`'s status ladder. Amounts are integer raw throughout; no
  float touches an amount, and `_RAW_RE`/`_UNIX_RE` are ASCII-only rather than
  `str.isdigit()`.
- `_safe_endpoint` drops the query string, fragment and userinfo before naming a
  node in a receipt, so an RPC key in `?key=` cannot reach a log. Verified
  against `https://user:pw@host/path?key=secret`.
- The `deadline` claim: `on_time` is computed but never added as a refusal, and
  both `README.md:61` and `SCHEMA.md` say in as many words that it is
  informational and a late payment is still accepted. The code matches the
  documentation.
- Same-terms replay: two deals with identical terms share a `deal_id`, so one
  block settles both. `README.md:27` tells the reader this ("Run it twice with a
  different `what_it_buys` and you get 409") and
  `test_happy_path` pins the idempotency. Documented, not a defect.
- Secrets: none in the tree. The library is read-only — it never holds a seed or
  a key and has no code that could.

## Found and fixed (this branch) — two money guards had no test

Mutation-ran the seven guards that decide whether XNO counts as paid, by
deleting each one and running the suite. Five are held by a test that names them
(the requested block's hash, `confirmed`, the amount, the destination, the
sender). **Two survived**, and both are on the path that decides what a `200`
means. No production code is changed here; the guards are live. What was missing
is anything keeping them that way.

**1. The previous block's hash check.** The amount is
`previous.balance - balance`, so the previous block has to be verified too, and
`read_send` does hash-check it. Deleting that check left the whole suite green —
`test_rpc_that_lies_about_contents_is_refused` covers only the *requested* block
— while `README.md` promises it: "the amount is the balance drop from the (also
hash-checked) previous block". Measured what the guard is worth, with it deleted
and a node serving a real block under the wrong hash for `previous`:

```
deal price      : 10000000000000000000000000000 raw (0.01 XNO)
actually moved  : 1 raw
status          : 200   paid: True
derived amount  : 10000000000000000000000000000
reasons         : []
```

A one-raw send, accepted as the full 0.01 XNO, with every other check passing: a
confirmed send from the asker to the answerer. Without that guard the node sets
the price. Two tests now hold it — one that the unverifiable `previous` is
refused by name, and one that pins the inflation case above.

**2. `verify_payment`'s reuse pre-check.** Replacing
`if owner is not None and owner != deal.deal_id:` with `if False:` also left the
suite green, because `accept_and_settle` catches reuse a second time at
`ledger.claim`. The two are **not** duplicates: `claim` is only reached when the
payment verifies, so without the pre-check a block that already settled another
deal reads as **503** while the node is unreachable, or **402** while the block
is unconfirmed, instead of **409** — a settled block's answer turning into
"retry". The new test asserts the 409, that it costs zero RPC calls, and that it
still holds against a node that knows nothing.

Failing-then-passing: each mutation is now killed by the test that names it
(2 failures and 1 failure respectively), and the unmutated tree is
**35 tests, OK (skipped=1)**.

## Could not verify

- **Anything a lying RPC could do about `confirmed`.** Confirmation is the one
  field taken from the node, as the module docstring says. Everything else is
  derived locally, but a node that lies about `confirmed` is outside what this
  library can check, and no second source is consulted.
- **`previous` pointing at another account's chain.** `read_send` does not
  compare the previous block's `account` with the requested block's. With an
  honest node it cannot differ — Nano will not confirm a block whose `previous`
  is not its own account's frontier — and with a dishonest node `confirmed` is
  already forgeable, so this adds nothing either way. Recorded rather than
  changed.
- **The `regex` acceptance over a 1 MB deliverable.** A catastrophic pattern
  (512 characters are allowed) can hold a thread. Reachable only through the
  optional HTTP helper, which takes the deal from the request body and which the
  README says to bind to localhost. Noted as an observation, not fixed: it is
  not an XNO path and a limit would be a new feature.
- No XNO moved and nothing was signed. The library cannot sign.
