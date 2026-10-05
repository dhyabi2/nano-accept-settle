# nano-accept-settle — audit 2026-10-05

Read through the one question that matters here: can an agent get paid in XNO with this,
today, without being hurt? No code is changed by this pass. One finding is recorded for
the owner rather than patched, because fixing it is a design decision and not a fix.

## Checked

- `python3 -m pytest tests/test_offline.py -q` — 31 tests green.
- `python3 -m pytest tests/test_live.py -q` — 1 test green against mainnet.
- `python3 -m py_compile nano_accept_settle/*.py tests/*.py` — clean.
- **The default RPC was reachable from this container today**, so the README quickstart
  was run end to end for real, which the 2026-10-04 note in the README could not do
  (`rpc.nano.to` answered that session with `429 api_key_required_for_heavy_usage`). The
  package was built and installed into a clean venv and the quickstart run **from outside
  the source tree**, verbatim:

  ```
  200 True True []
  ```

  which is exactly what the README comment promises. The example block
  `B749B757…04C9A58F` really is a confirmed 0.0005 XNO send, and `terms()` renders
  `amount_xno` as `0.0005` from `500000000000000000000000000` raw by integer `divmod`.
- The README's second claim also holds: a *different* deal presenting the same block gets
  `409 ['block already settled another deal']`. And the deal that **owns** the block stays
  `200 True` on the second and third call, so an answerer retrying after a timeout is not
  punished with a 409 — the claim is idempotent for its owner, which is the behaviour that
  matters on a flaky network.
- `terms()` round-trips: `Deal.from_dict(deal.terms()).deal_id == deal.deal_id`, with
  `pay_to` checked against the answerer and `amount_xno` ignored on input, as SCHEMA.md says.
- **The RPC is not trusted for anything but confirmation.** `read_send` recomputes the
  state-block hash locally from the returned contents and refuses contents that do not
  hash to the requested block; the amount is the balance *drop* against the previous
  block, which is hash-checked the same way; the payee is derived from `link`, not from
  the node's `subtype`/`amount` summary. A non-state block, or a previous block that
  cannot be verified, raises rather than being read. This is the strongest shape of this
  check I have seen in the Tier 0 set.
- Money is integers throughout: `amount_raw` must match `^[1-9][0-9]*$` (deliberately not
  `str.isdigit()`, which admits non-ASCII digits) and is bounded below `1 << 128`; the
  comparison against the ledger is string-to-string on canonical decimal; no float
  touches an amount.
- Fail-closed ordering in `accept_and_settle`: the acceptance test runs first (422), then
  reuse (409), then an RPC failure (503, with nothing recorded), then unpaid (402 with
  terms), and the block is claimed in the ledger **only** on the way to 200 — via
  `BEGIN IMMEDIATE` plus a primary key, so two threads cannot both claim it.
- `Rpc` refuses any action but `block_info` by name. There is no send, signing or key
  anywhere in the package.
- Secrets: none in the tree. `_safe_endpoint` deliberately strips userinfo, query and
  fragment before a node URL reaches a receipt's `reasons`, which is what keeps an
  `rpc.nano.to?key=…` out of whatever log reads the HTTP response.
- `nano.py`'s address codec: `key >> 256` is the correct and sufficient pad-bit check
  (equivalent to constraining the first body character to '1' or '3'), and the checksum
  is blake2b-5 reversed, per spec.

## Found, not fixed — for the owner

**The optional HTTP server can be hung by one request, because it takes the regex
*pattern* and the *deliverable* from the same body.**

`http.py` serves `POST /accept-and-settle` with a caller-supplied `deal`.
`_check_acceptance_spec` validates that `acceptance.pattern` compiles and is at most 512
characters, which a catastrophically backtracking pattern passes easily. Python's `re`
has no timeout, so `check_acceptance` then matches it against a deliverable of up to 1 MB.

Measured here with `^(a+)+b$` — 9 characters — against a string of `a`s:

```
18 chars -> 0.01s
20 chars -> 0.04s
22 chars -> 0.17s
24 chars -> 0.72s
```

Four times the work per two characters, so roughly 2x per character: about 40 characters
is hours, and about 50 is indefinite. One request of a few hundred bytes pins its thread
for as long as the process lives, and `ThreadingHTTPServer` gives each request a thread,
so a handful of them exhaust the host.

Scope, honestly:

- **The library used directly is not affected** — the quickstart shape, where your own
  code constructs the `Deal`, never takes a pattern from a stranger.
- The server binds `127.0.0.1` by default and `http.py` says to put it behind your own
  proxy. But the README's own advice for a marketplace is to "share one ledger (one
  database file, **or one HTTP service**) per marketplace", which is exactly the
  deployment where the body comes from someone else.

It is left open rather than patched because every fix is a decision, not a correction:
cap the deliverable far lower for the `regex` kind, run the match in a subprocess under a
wall clock, reject nested quantifiers in the pattern, or drop caller-supplied patterns on
the HTTP path and accept only `sha256`/`required_keys` there. Picking one changes what
the service accepts, which is the owner's call.

## Could not verify

- Block **signatures** are not verified by this package and were not verified here — it
  needs ed25519-blake2b, which is not in the standard library, and the README says so in
  its Limits section. `confirmed` is the node's word; the contents are hash-checked.
- Only one RPC endpoint was asked. The README's own advice for high value — two
  independent nodes, or a node you run — is not something this run could exercise.
- The 429 path the README documents at length did not reproduce today, so the receipt
  wording for it was read but not observed live.
