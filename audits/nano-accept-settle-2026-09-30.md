# Audit 2026-09-30

First audit of this repository.

## Checked

- `python3 -m unittest discover -s tests`: 23 offline laws passed before any change, and the full
  suite including the read-only live check passed too — so the block in the README quickstart
  (`B749B757…`) really is a confirmed 0.0005 XNO send that settles the example deal.
- `nano.py` against the Nano primitives it reimplements: the base32 alphabet, the 5-byte
  reversed-blake2b address checksum, and the state-block preamble
  (`6 || account || previous || representative || balance || link`). Round-tripped addresses and
  reproduced the known address in the tests.
- `read_send`: the claim that the RPC is not trusted holds. Contents are re-hashed against the
  requested hash, the previous block is re-hashed too, and the amount is the balance drop rather
  than the node's `amount` field. Only `confirmed` is taken from the node, which the README says.
- `verify_payment` / `accept_and_settle` status table against SCHEMA.md: 200/402/409/422/503 all
  match, and a block is claimed in the ledger only on a 200.
- `UsedHashLedger.claim` under `BEGIN IMMEDIATE`, and that a second instance on the same file
  sees the claim.
- `Deal.terms()["amount_xno"]` decimal formatting at 1 raw, 0.01 XNO and whole XNO.
- Non-custodial claim: no signing, no seed handling, no key material anywhere in the tree. The
  RPC client refuses any action but `block_info`.

## Found and fixed

**A misspelled field inside `acceptance` was ignored rather than refused, which silently changed
the acceptance test.** `Deal.from_dict` refuses unknown fields at the top level, and SCHEMA.md
opens with "Unknown fields are refused, so a typo can never silently change the terms" — but
`_check_acceptance_spec` never checked the keys of the `acceptance` object. Writing
`full_match` instead of `fullmatch` was accepted, and `check_acceptance` then used `re.search`
instead of `re.fullmatch`: a deal whose test was `\d+` over the whole deliverable passed on
`"sorry, no data: 42"`. It also changes `deal_id`, so the two sides were not even describing the
same deal.

`fullmatch` was also read for truthiness, so `"fullmatch": "false"` — a non-empty string — applied
fullmatch instead of skipping it.

Fixed by pinning the allowed key set per shape (`_ACCEPTANCE_FIELDS`) and requiring `fullmatch`
to be a boolean. This is a tightening: an `acceptance` object carrying extra keys that used to be
accepted is now refused, which is the documented intent.

## Not changed, worth knowing

- **The optional HTTP server will run a caller-supplied regex over a caller-supplied 1 MB body**,
  so a pathological pattern (at most 512 characters, which is ample for `(a+)+$`) is a CPU
  denial of service for whoever runs it. The README already says to bind it to localhost and it
  is off by default, and every caller supplies its own deal, so this is a deployment note rather
  than a defect in the library. Worth a timeout or a pattern check if the server is ever exposed.
- `accept_and_settle` is called from `do_POST` without a `try`, so an unexpected exception (a
  `sqlite3` error, say) closes the connection with no JSON body instead of a 500. Everything
  `verify_payment` can raise is already caught inside it, so nothing reachable was found.
- `amount_raw.isdigit()` is true for non-ASCII digits, so `Deal(amount_raw="٣")` is accepted and
  can then never be paid, because the on-chain amount is compared as the ASCII string `"3"`.
  Nothing in the package produces such a value; a caller would have to write it deliberately.
