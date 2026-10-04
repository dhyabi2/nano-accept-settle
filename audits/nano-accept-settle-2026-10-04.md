# nano-accept-settle — audit 2026-10-04

Lens: can an agent confirm it got paid in XNO with this, today, without being hurt.
Read end to end: `nano_accept_settle/nano.py`, `core.py`, `http.py`, the CI workflow,
`SCHEMA.md` and every claim in the README.

Base: `main` at `ca49e7a`.

## Checked

- **`nano.py` address and hashing.** `address_to_pubkey` checks the 60-character body, that
  the 52-character key does not exceed 256 bits, and the 5-byte blake2b checksum reversed.
  `state_block_hash` builds preamble 6 + account pubkey + previous + representative pubkey +
  balance as 16 big-endian bytes + link, blake2b-32. Both correct against the protocol.
- **The trust boundary in `read_send`.** The requested hash is recomputed from the returned
  contents, and the amount is the balance drop from the previous block, whose own hash is
  pinned inside those contents and re-checked. So a node cannot swap a block, move an
  amount, or change a destination: the only field taken on trust is `confirmed`, and the
  README's **Limits** says so.
- **Integer raw everywhere.** `_RAW_RE` is `^[1-9][0-9]*$` — no float, no leading zero, no
  non-ASCII digit (the comment names `٣`, which `str.isdigit()` accepts). `read_send`
  returns `str(delta)`, so the exact string comparison against `amount_raw` holds.
  `terms()` renders XNO by `divmod` against `10**30`, never a float.
- **Refusals on the verify path:** not a send, wrong sender, wrong destination, wrong
  amount, unconfirmed, bad hash shape, a block that already settled another deal. Each
  produces a reason and no record.
- **Fail-closed shapes.** An absent `confirmed` reads `str(None).lower() != "true"` → not
  confirmed. An account's first block (`previous` all zeros) gives `prev_balance = 0`, so
  `delta <= 0` and it is not a send — right, since an account must receive before it sends.
  An epoch or representative-change block has `delta == 0` and is not a send.
- **`UsedHashLedger` is the authority, not the read.** `verify_payment`'s `owner()` check is
  advisory; `accept_and_settle` claims under `BEGIN IMMEDIATE` and compares the returned
  owner, so two concurrent settles of one block cannot both reach 200.
- **`http.py` for input reaching a shell or a path.** It does neither: no subprocess, no
  `os.path` join from request data, path routing is one `==` comparison, the body is capped
  at 2 MB by `Content-Length` before being read, `b64decode(validate=True)`, and every
  parse error is a 400. The ledger path comes from `--db` or the environment, never a
  request.
- **Secrets, tree and full history.** None. No key, token, seed or address with a secret
  beside it.
- **CI.** Both jobs are real: the suite runs on 3.8-3.13, and a separate job builds the
  wheel and imports it from a temporary directory, which is what catches a module missing
  from `[tool.setuptools] packages`. The offline job then greps its own log for
  `OK (skipped=1)` so that CI cannot quietly start calling a public node.
- **Every README API claim against the code**, including the honest ones: `deadline` is
  informational, confirmation is the node's word, the ledger is local.

## Found

**One defect, on the first step a new agent takes.**

`https://rpc.nano.to` — `DEFAULT_RPC`, `core.py:19` — answers this session

```
{"error": 429, "code": "api_key_required_for_heavy_usage",
 "message": "Free public RPC limit reached. Create an API key and upgrade ...",
 "usage": "0/10000", "usage_24h": 0}
```

reproducibly, on two bare `curl`s and through the library's own `Rpc` with its User-Agent,
**at zero usage**. It is the calling address that is refused, not a quota: the 10-03 audit
of this repository read the live ledger and ran the README quickstart successfully from
elsewhere the day before. So `tests/test_live.py` fails and the README quickstart — which
says "the example settles for real" — does not run, for a caller in a cloud container,
which is who this library is for.

`_block_info` reported that as `rpc error: 429` and nothing else (`core.py:282`). A 503
receipt whose only reason is `429` sends the reader to look at their own block hash: it
names neither the node that refused nor the fact that a key is wanted. The node supplies
`code` and `message` and both were dropped.

## Fixed

- `_rpc_error_detail` appends the node's `code` and `message` to its `error` when it sends
  them, and `_block_info` names the endpoint when the client has a `url`. A plain
  `{"error": "Block not found"}` from a bare callable still reads exactly as before — two
  control tests hold that both ways.
- The README's **Limits** gains the measured refusal, that `usage: 0/10000` means the
  address and not the quota, that retrying from the same place will not clear it, and the
  two-line way to point `Rpc` at another node or at a keyed URL. The **Tests** section says
  what the live check looks like when it is the node refusing.

31 offline tests pass (was 29). Reverting `core.py` alone, keeping the tests, fails exactly
the new assertion.

Nothing else was changed. No refusal was added or removed, no amount, address, key path or
stored row moved, and the default endpoint is **not** changed — which node to trust is the
owner's call, not a routine's.

## Could not verify

- **Whether the alternate nodes work.** `https://node.somenano.com/proxy`,
  `https://proxy.nano.rpc.blvd.run` and `https://secondary.nano.city` (the last two are
  already used by `dhyabi2/nano-mcp`) are each denied by this environment's own network
  policy — `CONNECT tunnel failed, response 403` from the agent proxy, before any node is
  reached. Only `rpc.nano.to` is reachable from here, and it refuses us. So this audit can
  say the default refuses a cloud address; it cannot say which node should replace it.
- **The live test.** It fails here for the reason above, not for anything in the code. The
  offline suite is the one that ran.
- **Signature verification.** Not done by this library, by design and stated in the README;
  nothing here checks that the asker's key actually signed the block.
