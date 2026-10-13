# nano-accept-settle - audit 2026-10-13

Base: `ec5a91c` (main, "read_send: the previous block must belong to the same account (#10)").
Suite before anything: `NANO_ACCEPT_SETTLE_OFFLINE=1 python3 -m unittest discover -s tests` ->
**37 tests, OK (skipped=1)**; `python3 -m py_compile nano_accept_settle/*.py` clean.

## Checked

- `read_send` end to end, which is the whole trust argument: the state-block hash recomputed
  locally from the returned contents, the previous block hash-checked **and** required to belong to
  the same account, the amount taken as the balance drop rather than from the node's `amount`.
  Walked the ways a node could inflate a payment and could not find one: to move the delta it must
  present a `previous` whose contents hash to `c["previous"]` and whose `account` matches, which is
  the real previous block. A first-block claim (`previous` all zeros) can only make a block **not**
  a send. That part is sound.
- `verify_payment`'s five refusals (not a send, wrong sender, wrong destination, wrong amount, not
  confirmed) and that `paid` is `not reasons`.
- `accept_and_settle`'s status ladder, and that the used-hash ledger is written only on a 200,
  through `claim`'s `BEGIN IMMEDIATE` with the owner returned afterwards.
- `nano.py`: address checksum both ways, `state_block_hash`'s field order and 16-byte balance.
- `Deal`: `amount_raw` as `^[1-9][0-9]*$` under `1 << 128`, no float anywhere on an amount,
  `_RAW_RE`/`_UNIX_RE` as ASCII-only rather than `str.isdigit()`, unknown-field refusals per
  acceptance shape, `terms()`'s exact decimal `amount_xno`.
- `http.py`'s request handling and the `deliverable_b64` path.
- The README.md and SCHEMA.md claims touching the above against the code. Not exhaustive: review of
  this audit found two of its own claims wrong (the canonical-JSON rule as first written omitted the
  ASCII-only escaping and float form; the regex limit as first written said other threads keep running),
  both corrected below.

## Found and fixed (documentation, with a test pinning the behaviour)

**A correct answer delivered as JSON fails its `sha256` acceptance, and SCHEMA.md said otherwise.**

SCHEMA.md read: "`sha256`: the deliverable's bytes (text is UTF-8 encoded) must hash to exactly
this value." A JSON object has no bytes of its own by the time `check_acceptance` sees it -
`{"deliverable": {...}}` over `nano_accept_settle.http` arrives already parsed - so `_as_bytes`
(`core.py:195-200`) invents them: `json.dumps(sort_keys=True, separators=(",", ":"))`, the same
rule as `deal_id`. Nothing said so. Measured:

    answerer's bytes, and what they hashed : {"temp_c": 21, "city": "Muscat"}
    what the library hashes                : {"city":"Muscat","temp_c":21}
    check_acceptance(deal, the object)     : passed False  ("sha256 mismatch: got 7ef6769a...")
    check_acceptance(deal, their bytes)    : passed True

So an answerer who follows the documented rule, hashes the JSON text they put on the wire, and
sends that answer the natural way over HTTP gets a **422** - a correct answer refused, and an asker
who does not pay for it. There is no way to recover their original spacing at acceptance time, so
the fix is the contract, not the code: both sides agree the `sha256` before the work starts, and
the rule has to be knowable then.

SCHEMA.md and README.md now state the rule per deliverable type, define canonical JSON exactly
(Python's `json.dumps(value, sort_keys=True, separators=(",", ":"))`, which is ASCII-only with
`\uXXXX` escapes and writes floats in `repr`, so `1e2` is hashed as `100.0`), show the mismatch,
and name the two ways out (send the bytes you hashed as text or `deliverable_b64`, or hash the
canonical form). A 422 records nothing (`accept_and_settle` returns before `verify_payment` and the
ledger), so the same block hash can be resent that way.
`test_a_json_deliverable_is_hashed_as_canonical_json_not_as_the_text_it_arrived_in` pins it in both
directions, and `test_canonical_json_is_ascii_escaped_and_floats_are_python_repr`
pins the escaping and float form. Suite after: **39 tests, OK (skipped=1)**.

**No code changed, so there is no failing-then-passing run for this.** The behaviour was always
this; the document was wrong about it. The new test is what stops the document going wrong again:
the canonical form is now a promise to two strangers, and changing it would break every live
`sha256` deal.

## Found, documented as a limit, NOT fixed - needs a person's call

**A `regex` acceptance runs the asker's pattern, and a pattern can be made expensive.** Python's
`re` backtracks. Measured, `{"type": "regex", "pattern": "^(a+)+$", "fullmatch": true}`:

    18 a's + b ->   0.011 s
    22 a's + b ->   0.219 s
    26 a's + b ->   3.035 s
    28 a's + b ->  12.831 s
    30 a's + b ->  53.194 s

Doubling per character, well under both existing caps (512-character pattern, 1 MB deliverable).
And it **stalls every thread in the process**: CPython's `re` holds the GIL for the whole match.
Measured: a `sleep(0.05)` in another thread woke only when a 1.6 s match at 25 characters finished;
over the `ThreadingHTTPServer` in `nano_accept_settle.http`, a benign request sent during that match
waited 1.3 s for it. (An earlier draft of this audit said `re` yields and other threads keep
running; that was wrong.) So one `POST /accept-and-settle` can freeze the shared HTTP verifier the
README recommends for a marketplace for as long as its author likes, and that verifier takes its
deals from whoever is asking.

Not fixed in code, deliberately. Every option is worse than the limit: refusing nested quantifiers
is a heuristic that both refuses legitimate patterns (`(\d+\.?)+`) and misses equivalent ones
(`(a|a)+$`), so it would read as a fix while leaving the hole; capping the deliverable does not
help, since 30 characters is already a minute; and `re` has no timeout, with `signal.setitimer`
unavailable off the main thread, which is exactly where the threaded HTTP server runs it. A
subprocess per match would close it and would also end this library's "standard library, no moving
parts" character - that is a maintainer's decision, not a routine's. It is recorded under
**Limits** with the measurement and the operational mitigation (a per-request time limit on the
regex check in a separate, killable process, or a pattern length/complexity limit plus a concurrency
limit in front of an exposed verifier - a client-side timeout does not stop the match; `sha256` or
`required_keys` for deals from strangers, both linear and unable to backtrack).

## Could not verify

- The live check (`tests/test_live.py`, 1 skipped offline) reads one historical block from
  `https://rpc.nano.to`, which refuses this container's address with `429
  api_key_required_for_heavy_usage` - the condition the README's last Limit already documents. So
  the README quickstart was not run end to end from here; its block hash and amounts were read, not
  confirmed against the ledger.
- Signature verification is out of scope for the library by design (no ed25519-blake2b in the
  standard library), so a node that fabricates blocks is still trusted. Unchanged, and documented.
