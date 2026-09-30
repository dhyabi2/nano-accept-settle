# Deal schema (v1, pinned)

A deal is a JSON object. Unknown fields are refused, so a typo can never silently change the terms.

| Field | Required | Type | Meaning |
|---|---|---|---|
| `asker` | yes | string | Nano address (`nano_` or `xrb_`, checksum verified) that pays. |
| `answerer` | yes | string | Nano address that is paid. Must differ from `asker`. |
| `amount_raw` | yes | string | Exact price in raw, decimal digits only, no leading zero. 1 XNO = 10^30 raw. Floats are refused. |
| `acceptance` | yes | object | The acceptance test, one of the three shapes below. |
| `deadline` | no | string or int | ISO 8601 with a timezone (`2026-12-31T00:00:00Z`) or unix seconds. Informational: a payment the node first saw after it is still accepted, and reported as `payment.on_time: false`. |
| `what_it_buys` | no | string | Plain words for what the asker is paying for. Same name and meaning as the field in bounded task specs, so a bounded task can be passed through unchanged where the fields overlap. |
| `deal_id` | no | string | If given, must equal the computed id (below); otherwise the deal is refused. |
| `amount_xno` | no | string | Accepted on input and ignored (it appears in 402 terms for humans). `amount_raw` is the only amount that counts. |

`deal_id` = sha256 of the canonical JSON (sorted keys, no whitespace) of
`{asker, answerer, amount_raw, acceptance, deadline, what_it_buys?}` after addresses are normalised to `nano_`
and the deadline to unix seconds. Two parties who agree on the terms compute the same id.

## Acceptance tests

```json
{"type": "sha256", "sha256": "<64 hex>"}
{"type": "required_keys", "required": ["price", "unit"], "types": {"price": "number"}}
{"type": "regex", "pattern": "^\\d+$", "flags": "i", "fullmatch": true}
```

- `sha256`: the deliverable's bytes (text is UTF-8 encoded) must hash to exactly this value.
- `required_keys`: the deliverable must be a JSON object containing every key in `required`; `types` optionally
  pins top-level key types (`string`, `number`, `integer`, `boolean`, `object`, `array`, `null`; a boolean is
  never a number). This is a deliberately small subset of JSON Schema, not a validator.
- `regex`: Python `re` over the UTF-8 text; `re.search` by default, `re.fullmatch` when `fullmatch` is true.
  `flags` may contain `i`, `m`, `s`. Pattern at most 512 characters, deliverable at most 1 MB. `fullmatch`
  must be `true` or `false`, never a string.

Unknown fields are refused inside `acceptance` too, one shape at a time: `sha256` takes `type` and `sha256`;
`required_keys` takes `type`, `required` and `types`; `regex` takes `type`, `pattern`, `flags` and
`fullmatch`. A misspelling is not ignored, because ignoring it would change the test the deal was written
to run: `full_match` for `fullmatch` turns a whole-string test into a substring search.

## Receipt (what `accept_and_settle` returns)

```json
{"status": 200, "deal_id": "...", "accepted": true, "paid": true, "block": "B749...",
 "reasons": [], "payment": {"account": "nano_...", "destination": "nano_...", "amount_raw": "...",
 "confirmed": true, "is_send": true, "local_timestamp": 1790150110, "on_time": true}}
```

| status | meaning |
|---|---|
| 200 | acceptance test passed and the payment is a confirmed send asker -> answerer of exactly `amount_raw`; the block is now recorded against this deal |
| 402 | test passed, payment not (yet) valid: `reasons` says why, `terms` carries the deal and `pay_to` |
| 409 | that block already settled a different deal |
| 422 | acceptance test failed (the RPC is not consulted) |
| 503 | the RPC could not be read or returned a block that does not hash to the requested hash; nothing recorded, retry |
