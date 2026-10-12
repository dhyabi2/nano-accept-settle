# nano-accept-settle — audit 2026-10-12

Lens, unchanged: can an agent get paid in XNO with this code today without being hurt?
This repository is the piece that decides whether a `200` means the money arrived, so the
whole audit is spent on the five guards behind that `200`.

Starting point: `main` at `b51c3cb`, **35 tests, OK (skipped=1)** before any change.

## What I checked

- Installed from the tree and ran both of the repository's own commands:
  `python -m unittest discover -s tests` and the CI shape
  (`NANO_ACCEPT_SETTLE_OFFLINE=1 ...`, which CI additionally greps for `OK (skipped=1)`).
- Read the XNO path end to end: `Deal` → `terms()` → `check_acceptance` → `verify_payment`
  → `read_send` → `UsedHashLedger.claim` → the receipt status, and `nano.py`'s address and
  state-block hashing underneath it.
- Amount handling: no float touches an amount anywhere. `amount_raw` is a decimal string
  matched against `^[1-9][0-9]*$` and range-checked under `1 << 128`; the derived amount is
  `str(prev_balance - balance)` on Python ints; `terms()["amount_xno"]` is built by
  `divmod(..., 10**30)` and string padding, never division. Re-checked the `rjust/rstrip`
  formatting at the two ends that bite — 1 raw gives
  `0.000000000000000000000000000001` and `10**29` raw gives `0.1`.
- The guards that gate a `200`, by deleting each and re-running: the requested block's hash,
  `confirmed`, the amount, the destination, the sender, the `previous` hash, and the ledger
  reuse pre-check. All seven are held by a test that names them — the 10-08 audit's work
  stands.
- An open block can never read as a send (`previous == 0*64` sets `prev_balance = 0`, so
  `delta <= 0`), and a representative change reads as `delta == 0`. Both correct.

## Found and fixed — the `previous` hash check was only half a guard

`read_send` (`nano_accept_settle/core.py:353`) hash-checks the previous block, which the
10-08 audit added after measuring that a node could otherwise set the price. It never
checked **whose** block it is. A block's `previous` is by definition the account's own head,
so the missing half matters: a node does not have to forge anything to defeat the hash
check — it can name any genuine high-balance block on the public ledger as `previous`, under
its own real hash.

Measured against the unmodified tree, with the asker's chain never consulted at all:

```
status      : 200
paid        : True
derived amt : 10000000000000000000000000000      (the deal's full 0.01 XNO)
reasons     : []
previous blk account: nano_3bgkorn8t5ey...   (a stranger)
this  blk account   : nano_3dt8oa5m1yk9...   (the asker)
```

A `200`, full amount, for a send that moved none of the asker's money. This is the same
threat model the two existing lying-node tests already cover, and it makes the 10-08 guard
close to free to satisfy, which is why it is a defect rather than a hardening.

**Fix:** one added refusal in `read_send` —
`normalize_address(pc["account"]) != normalize_address(c["account"])` raises
`previous block belongs to another account`. It narrows what is accepted and nothing else:
no amount, destination, rounding, address or key path is touched, and no refusal is removed.

**Failing-then-passing:** with the two new tests and `core.py` reverted, exactly those two
fail (`AssertionError: 200 == 200 : a stranger's balance was accepted as
10000000000000000000000000000 raw of the asker's money`). With the fix, **37 tests, OK
(skipped=1)**. Deleting the new line alone fails both again.

The README promised "the amount is the balance drop from the (also hash-checked) previous
block", which was true and insufficient; it now says the previous block must also belong to
the same account, and why.

## Could not verify

- Block **signatures** are still not verified — it needs ed25519-blake2b, outside the
  standard library, and the README's Limits says so. `confirmed` remains the node's word.
  This audit's fix removes one way a node can lie about an amount; it does not remove the
  node from the trust boundary.
- Only the mocked RPC was exercised. `https://rpc.nano.to` still refuses this container's
  address with `429 (api_key_required_for_heavy_usage)` at zero usage, as the 10-04 audit
  recorded, so the live check could not be run from here. It is skipped, not failing, in the
  offline mode CI uses.

## Still open, unchanged — for the owner

The 10-05 audit's finding stands: `nano_accept_settle.http` takes the regex *pattern* and
the *deliverable* from the same request body, and Python's `re` has no timeout, so one small
request can pin a thread indefinitely (`^(a+)+b$`, ~50 characters of input). Every fix is a
decision about what the service accepts, not a correction, so it is not patched here.
