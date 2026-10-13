# nano-accept-settle — audit 2026-10-13

Lens, unchanged: can an agent get paid in XNO with this code today without being hurt?
This repository decides whether a `200` means the money arrived, so the audit is spent on
the five guards behind that `200` and on what the receipt says when a guard cannot run.

Starting point: `main` at `ec5a91c` ("read_send: the previous block must belong to the same
account", #10). `python3 -m pytest -q` → **37 passed** before any change, 40 after.

## Checked

- `Deal` / `Deal.from_dict` / `SCHEMA.md`: amount is an ASCII-digit raw string (`_RAW_RE`),
  no float reaches an amount, `deal_id` is the hash of the canonical terms and is checked
  when the payer sends it back, `pay_to` must be the answerer, unknown fields are refused
  rather than ignored. `terms()["amount_xno"]` re-derived by hand for 1 raw, 10 raw,
  `10**29`, `10**30` and `10**30 + 1`: exact in every case, no float anywhere.
- `read_send`: the block hash is recomputed locally from the six state-block fields, the
  amount is the balance drop from the previous block, the previous block is hash-checked
  **and** must belong to the same account (#10). All four lying-node tests re-read; the
  guards hold.
- `verify_payment`: sender, destination, exact amount, `confirmed`, and the used-hash
  ledger ahead of the RPC. `accept_and_settle` records a block only on a 200, through
  `BEGIN IMMEDIATE`, and re-checks the claim's owner so a race lands on 409.
- `nano_accept_settle.http`: body cap, base64 validated, `Deal.from_dict` errors → 400.
- `nano.py`: address checksum both ways, `xrb_`/`nano_` normalised, the known live address
  from block `B749…A58F` still round-trips.
- The README's quickstart and every documented status.

## Found and fixed (#11)

**A reply the node itself got wrong was charged to the payer** — `nano_accept_settle/core.py:347`
(`state_block_hash(c)`), `:355` (the same call for `previous`) and `:375`
(`int(info.get("local_timestamp") or 0)`).

`read_send` hands the node's `contents` straight to `state_block_hash`, which reads six
fields and raises whatever the bad one raises. Two outcomes, both wrong, both measured
against a mocked node this run:

| the node sent | before |
| --- | --- |
| `"balance": null`, `{}`, `[]` | **uncaught `TypeError`** — `accept_and_settle` left with a traceback; `nano_accept_settle.http` drops the connection with no reply at all |
| `"balance": "0x10"`, a figure past `2**128`, no `balance` key, `"previous": "xyz"`, `"link": null`, `"account": null` | **402 with `terms`** |
| `"local_timestamp": [1]` or `{"a": 1}` | **uncaught `TypeError`**, on a payment that was otherwise fully verified |

`verify_payment` catches `RpcError, KeyError, ValueError, AddressError` — not `TypeError`,
hence the first and third rows. The second row is the worse one: those four exceptions are
caught but are not `RpcError`, so `rpc_error` stays false and the receipt comes back **402,
and a 402 carries `deal.terms()`**. That tells the payer their money has not arrived and
hands them the amount to send again — for a block this code never managed to read. A
sender who believes the receipt pays twice.

**Fix:** one helper, `_state_hash(contents, which)`, wraps both `state_block_hash` calls and
turns `AddressError`, `KeyError`, `TypeError` and `ValueError` into `RpcError` → **503,
"try again, nothing was recorded"**, which is what the other two malformed-reply refusals
in `read_send` already return. A second helper, `_local_timestamp`, reads an unreadable
clock figure as absent (0 → `on_time: null`), which the README already documents as a node's
normal behaviour for old blocks — that figure decides nothing about the money, and a
payment that passed all five money guards should not be lost over it.

No money test moves: no amount, destination, rounding, address, key path or acceptance test
is touched, and no refusal is removed. A reply that hashes is hashed exactly as before, and
a hash that does not match the requested block is still refused with the message it always
had.

**Failing-then-passing:** three new tests. Against `main`'s `core.py` exactly those three
fail — two with `TypeError: int() argument must be a string … not 'NoneType'` / `'list'`,
one asserting `status 503, not 402` and `assertNotIn("terms", r)`. With the fix: **40
passed**. Reverting either helper alone fails its own test again.

## Could not verify

- Block **signatures** are still not verified (needs ed25519-blake2b, outside the standard
  library; the README's Limits says so). `confirmed` remains the node's word. This fix
  changes nothing about that trust boundary — it only stops a garbled reply from being
  read as a verdict on the payment.
- Only the mocked RPC was exercised. `https://rpc.nano.to` still refuses this container
  with `429 (api_key_required_for_heavy_usage)` at zero usage, as the 10-04 audit recorded,
  so the live test is skipped rather than failing in the offline mode CI uses.

## Still open, unchanged — for the owner

The 10-05 finding stands: `nano_accept_settle.http` takes the regex *pattern* and the
*deliverable* from the same request body and Python's `re` has no timeout, so one small
request (`^(a+)+b$`, ~50 characters) can pin a thread indefinitely. Every fix for it is a
decision about what the service accepts, not a correction, so it is not patched here.
