# nano-accept-settle - audit 2026-10-10

Nothing changed. The settle path was read end to end and no defect was found;
this file is the record of what was looked at, so the next pass can start
somewhere else.

## Checked

- `NANO_ACCEPT_SETTLE_OFFLINE=1 python3 -m unittest discover -s tests`:
  **35 passed, 1 skipped** (the skip is the live ledger read, which CI also
  asserts really was skipped). `python3 -m compileall` clean.
- **The amount.** `RAW_PER_XNO = 10 ** 30` and every amount is an integer
  string compared as a string of canonical decimal digits. `_RAW_RE` is
  `^[1-9][0-9]*$`, so no leading zero and no zero amount can be built, and it
  is a deliberately ASCII test rather than `str.isdigit()` - `"٣"` is a
  digit to `isdigit` and would make a deal whose amount can never equal the
  ASCII string read off the ledger. `terms()["amount_xno"]` is built by
  `divmod` against `RAW_PER_XNO` and `rjust(30, "0")`, not by division: no
  float occurs anywhere on the money path (`grep -n "float\|/ [0-9]\|1e"` over
  `nano_accept_settle/` finds none).
- **The payment is derived, not taken.** `read_send` (`core/py:337`) recomputes
  the state-block hash locally from the returned `account`, `previous`,
  `representative`, `balance` and `link` and refuses contents that do not hash
  to the hash that was asked for; the amount is the balance drop from the
  previous block, whose hash is checked the same way. The node's own `amount`
  and `subtype` are carried in the receipt for the reader but are not what any
  decision is made on. `confirmed` is the one thing taken from the node, and
  the README says so under **Limits**.
- **What is refused.** `verify_payment` requires a send, from the asker, to the
  answerer, of exactly `amount_raw`, confirmed, and not already the settlement
  of another deal. A `previous` block that is not a verifiable state block
  refuses rather than assuming (so a legacy-block predecessor is a refusal, not
  a guess), and an epoch or change block has a zero balance delta and is not a
  send. Each of these has a test, including
  `test_a_forged_previous_balance_cannot_inflate_the_amount` and
  `test_rpc_that_lies_about_contents_is_refused`.
- **One payment settles one deal.** `UsedHashLedger.claim` does the read and
  the insert inside one `BEGIN IMMEDIATE` and returns the deal that owns the
  block afterwards, so a race ends in 409 rather than two 200s.
  `verify_payment` never records; only `accept_and_settle` does, and only on a
  clean 200. Re-presenting the *same* deal is idempotent, which is what an
  asker who lost the response needs.
- **The acceptance test runs before the node is consulted**, so a failing
  deliverable is 422 with no `payment` key at all - the absence is the proof.
  `_ACCEPTANCE_FIELDS` refuses an unknown key per shape rather than ignoring
  it, which matters because a silently-ignored `full_match` would turn a
  whole-string test into a substring search and would change the `deal_id`.
- **Addresses.** `nano.py` checks the 5-byte blake2b checksum, refuses a
  public key over 256 bits (the four pad bits of the 52-character body), and
  normalises `xrb_` to `nano_` before any comparison, so one account in two
  spellings is one account. The 8-character checksum field decodes to at most
  40 bits, which is exactly the 5 bytes it is converted to, so no overflow is
  reachable there.
- **Secrets.** `_safe_endpoint` drops the query string, the fragment and any
  userinfo before a node is named in a receipt's `reasons`, which are returned
  over HTTP by `nano_accept_settle.http`; `rpc.nano.to` takes `?key=` and a
  private node may carry `user:password@`. Tested by
  `test_the_node_is_named_without_its_credentials`. No secret of any kind is in
  the tree.
- **The HTTP server** caps the body at `MAX_BODY` and refuses a missing,
  non-numeric or oversized `Content-Length` with a 400 before reading
  anything; a malformed request is 400 and never reaches the ledger.
- **README against the tree.** The documented statuses (200/402/409/422/503)
  are the ones `accept_and_settle` returns; the wheel is built and exercised
  from outside the checkout by `.github/installed_check.py`, so a module left
  out of `[tool.setuptools] packages` cannot hide behind the source tree; the
  CI matrix runs every interpreter `requires-python = ">=3.8"` admits. The only
  external URLs are `rpc.nano.to` and this repository.

## Found

Nothing. No change is proposed.

## Could not verify

- The live check (`tests/test_live.py`) reads one historical mainnet block from
  `https://rpc.nano.to`, which answers a cloud address with
  `429 api_key_required_for_heavy_usage` at `usage: 0/10000` - the address is
  refused, not the usage. That is already documented under **Limits** and the
  receipt repeats the node's own message, so the condition is reported rather
  than hidden. The offline suite is what was run here, as CI does.
- Signature verification is outside this package by design (it needs
  ed25519-blake2b, which the standard library does not have), so a block's
  contents are hash-checked but not signature-checked, and `confirmed` is the
  node's answer. Both limits are stated in the README, with the remedy - point
  `Rpc(url)` at your own node, or require two independent ones - and this is a
  design boundary, not a defect.
