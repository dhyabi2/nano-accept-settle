# nano-accept-settle — audit 2026-10-03

Python 3.11.15, standard library only. `python3 -m pytest -q` → **29 passed**, 0 failed, with the
live mainnet check *included* (it was skipped in both previous audits). Nothing to change: this
note records a pass that found no defect, and closes the gap the 2026-10-02 audit left open.

## Checked

- **The README quickstart, run verbatim.** It claims the pasted block prints `200 True True []`
  and that running it again with a different `what_it_buys` gives 409. Both are exactly true:

      RUN1: 200 True True []
      RUN2: 409 True False ['block already settled another deal']

  So the first step a new agent takes on this repository works, against the real ledger, including
  the one-payment-settles-one-deal claim.
- **The live ledger read, which the last two audits could not run.**
  `tests/test_live.py` reads block `B749B757…A58F` through `https://rpc.nano.to` and asserts sender,
  destination, `500000000000000000000000000` raw, `is_send` and `confirmed` — and that the
  balance-delta amount this library derives agrees with the node's own `amount` field. It passes.
  That is the whole non-custodial claim exercised end to end: the state-block hash is recomputed
  locally from the returned contents, the previous block is hash-checked too, and the amount is the
  drop between them rather than anything the node reported.
- `verify_payment`'s refusals, re-read against `read_send`: not-a-send, wrong sender, wrong
  destination, wrong amount either way, unconfirmed, a reused hash, and an RPC whose contents do
  not hash to the requested block (503, nothing recorded).
- `Deal.terms()["amount_xno"]` rendering at 1 raw, `10**29` raw and whole XNO — exact decimal
  strings, no float anywhere on an amount (`_RAW_RE` is `^[1-9][0-9]*$`, and `RAW_PER_XNO` is
  `10 ** 30`).
- `Deal.from_dict(deal.terms())` round trip, including the `pay_to` check added in #3.
- `nano.py` address and checksum handling: the 5-byte blake2b digest is recomputed, `key >> 256`
  is refused, and the 8-character checksum field cannot overflow its 5 bytes.
- `UsedHashLedger.claim` under `BEGIN IMMEDIATE`, and that a block is recorded only on a 200.
- Secret scan of the tree and all four commits: none.

## Found

Nothing worth changing. The three items the 2026-10-02 audit left under "worth knowing" were
re-read and all three are still accurate descriptions of deliberate, documented behaviour:
`amount_xno` is accepted-and-ignored on input exactly as `SCHEMA.md:14` pins it; the optional HTTP
server runs a caller-supplied regex over a caller-supplied body, which is a deployment note for a
localhost-bound, off-by-default server; and `deadline` is informational, which both `README.md:61`
and `SCHEMA.md:11` state plainly, with `on_time` reported in the receipt rather than enforced.

No PR is opened for this repository beyond this note.

## Could not verify

- Nothing material. The live check ran; the quickstart ran. No funds moved and none could: this
  package contains no signing code and `Rpc.__call__` refuses any action but `block_info`.
- The repository has no CI workflow, so the suite that passes here is not run on push. Worth
  adding (the other Python repository in this group has `.github/workflows/test.yml`), but adding
  one is not an audit fix.
