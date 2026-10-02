# nano-accept-settle — audit 2026-10-02

Python 3.11.15, standard library only, no install step. Baseline before any change:
`NANO_ACCEPT_SETTLE_OFFLINE=1 python3 -m unittest discover -s tests` → 27 tests, OK (1 skip,
the live mainnet read). `python3 -m py_compile nano_accept_settle/*.py` clean.

Read end to end through one question: can an agent pay, or be paid, for an answer with this
today without being hurt.

## Checked

- `Deal` construction and `Deal.from_dict` against every row of SCHEMA.md, including the
  round trip `Deal.from_dict(deal.terms())` — the one shape a payer is actually handed.
- `amount_raw` parsing: type, sign, leading zero, float, out of range, and what `str.isdigit()
  ` admits that `int()` and the ledger do not.
- `terms()["amount_xno"]` decimal rendering at 1 raw, 10**29 raw, 0.01 XNO and whole XNO. Exact
  in every case; no float touches an amount anywhere in the package (`grep` for `float(`: none
  on an amount path).
- `verify_payment`: sender, destination, exact `amount_raw`, confirmation, and that the amount
  is the balance drop between two locally hash-checked state blocks rather than the node's
  `amount` field.
- `accept_and_settle` status table against SCHEMA.md (200/402/409/422/503) and that a block is
  claimed in the ledger only on a 200.
- `UsedHashLedger.claim` under `BEGIN IMMEDIATE`; the claim result is compared to the deal id,
  so two deals racing for one block cannot both settle.
- Non-custodial claim: no signing, no seed, no key material; `Rpc.__call__` refuses any action
  but `block_info`.
- The HTTP handler's `except (ValueError, KeyError, TypeError, DealError)` around parsing, which
  is what kept the `int()` crash below from reaching the socket.

## Found and fixed

**1. `amount_raw` and a unix `deadline` accepted Unicode digits** (`core.py:125`, `core.py:38`).
`str.isdigit()` is true for `"٣"` (Arabic-Indic three), so a deal could be built whose amount can
never match the ASCII decimal string read off the ledger: pay exactly 3 raw and
`verify_payment` answers `amount is 3 raw, deal says ٣ raw` — a 402 on the one payment that
satisfies the deal, with nothing on the wire explaining it. `"²"` is also `isdigit()` but not
`int()`-able, so the constructor raised a bare `ValueError` that `except DealError` does not
catch (`DealError` is a `ValueError` *subclass*). Replaced with explicit ASCII patterns
(`^[1-9][0-9]*$`, `^[0-9]+$`); behaviour unchanged for every ASCII input.
Merged as #2, test `DealSchema::test_amount_and_deadline_need_ascii_digits`.

**2. The 402 terms body could not be parsed back** (`core.py:141` against `core.py:174`).
`terms()` adds `deal_id`, `amount_xno` and `pay_to`; `from_dict` allowed the first two and
refused the third as an unknown field. A payer that read the terms off a 402 and called
`Deal.from_dict(terms)` — the obvious way to recompute the deal id before sending money — got
`unknown deal fields: pay_to`. `pay_to` is now accepted and **checked**: it must normalise to
the answerer, so a terms body that points the payment somewhere else is refused rather than
silently ignored. Documented in SCHEMA.md. Test `DealSchema::test_402_terms_round_trip`.

## Not changed, worth knowing

- `amount_xno` is still accepted and ignored on input, as SCHEMA.md says. A hand-written terms
  object could therefore carry `amount_raw` and `amount_xno` that disagree; nothing in this
  package reads `amount_xno`, but a payer that displays or pays from it would pay the wrong
  amount. Tightening it to "must agree" would contradict the pinned schema, so it is the
  owner's call, not an audit fix.
- The optional HTTP server runs a caller-supplied regex over a caller-supplied 1 MB body, so a
  pathological pattern is a CPU denial of service for whoever runs it. Off by default and
  localhost-bound; a deployment note, as the previous audit said.
- `accept_and_settle` is called from `do_POST` without a `try`, so an unexpected `sqlite3` error
  would close the connection with no JSON body instead of a 500. Nothing reachable found.
- A non-`RpcError` `ValueError` out of `read_send` lands as 402 rather than 503. Only reachable
  if a node returns a block that hashes correctly yet carries an unparseable field.

## Could not verify

- The live mainnet check (`tests/test_live.py`) was not run: this audit ran offline. The
  README's quickstart block hash and the `rpc.nano.to` default were therefore read, not
  exercised.
