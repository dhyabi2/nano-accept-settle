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
| `pay_to` | no | string | Accepted on input; if given, must be the `answerer` (it appears in 402 terms as the address to pay). |

The last three are **derived**, not terms: `terms()` adds them to a 402 body, and `from_dict` takes that
body back unchanged, so a payer can recompute the deal id from exactly what it was handed.

`deal_id` = sha256 of the canonical JSON of
`{asker, answerer, amount_raw, acceptance, deadline, what_it_buys?}` after addresses are normalised to `nano_`
and the deadline to unix seconds. Two parties who agree on the terms compute the same id.

**Canonical JSON** here means exactly the bytes of Python's
`json.dumps(value, sort_keys=True, separators=(",", ":"))` - sorted keys, `,` and `:` separators, no
whitespace, **and ASCII-only output**: every non-ASCII character is written as a lowercase `\uXXXX` escape,
and a character above U+FFFF as an escaped surrogate pair (`"Zoë"` -> `"Zo\u00eb"`, `"😀"` ->
`"\ud83d\ude00"`). JavaScript's `JSON.stringify`, RFC 8785 (JCS) and `ensure_ascii=False` all write those
characters as raw UTF-8 instead, and produce a different hash for any value that contains one.

## Acceptance tests

```json
{"type": "sha256", "sha256": "<64 hex>"}
{"type": "required_keys", "required": ["price", "unit"], "types": {"price": "number"}}
{"type": "regex", "pattern": "^\\d+$", "flags": "i", "fullmatch": true}
```

- `sha256`: the deliverable's bytes must hash to exactly this value. **What "its bytes" means depends on
  how the deliverable arrives, and the two are not interchangeable:**
  - `bytes` are hashed as they are; a `str` is hashed as its UTF-8 encoding.
  - any other JSON value - an object or array, which is what `{"deliverable": {...}}` becomes over
    `nano_accept_settle.http`, and equally a bare number, `true`, `false` or `null` sent as `deliverable` -
    has no bytes of its own by the time it is seen, so it is hashed as **canonical JSON** (defined above,
    the same rule as `deal_id`). A JSON *string* sent as `deliverable` arrives as text and is hashed as its
    UTF-8, unquoted. Because the value was parsed before it was hashed, the canonical form is that of the
    parsed value, not of what was sent:
    - non-ASCII characters become `\uXXXX` escapes (surrogate pairs above U+FFFF), so
      `{"city":"مسقط","name":"Zoë"}` is hashed as
      `{"city":"\u0645\u0633\u0642\u0637","name":"Zo\u00eb"}`;
    - a number with a fraction or exponent becomes a Python float and is written in Python's `repr`:
      `1e2` is hashed as `100.0`, `1.10` as `1.1`, while `1.0` stays `1.0` and `100` stays `100`;
    - if an object repeats a key, the last value wins and the others are gone.

    If you need byte-exact control over what is hashed, do not send a JSON value: send the exact bytes you
    hashed as text (`"deliverable": "<the JSON text>"`) or as `deliverable_b64`.

  So an answerer who hashes the JSON text they put on the wire and sends that same answer as an object
  gets a mismatch, because the whitespace and key order they hashed are gone:

  ```
  on the wire, and hashed  : {"temp_c": 21, "city": "Muscat"}
  what this hashes          : {"city":"Muscat","temp_c":21}
  ```

  That is a **422** - a correct answer refused, and an asker who does not pay for it. An answerer who
  wants their own bytes hashed must send them as text, or as `deliverable_b64` over HTTP; an answerer
  hashing a JSON value must hash it the canonical way. Both sides agreed the `sha256` before the work
  started, so this is worth settling then, not at acceptance.

  A 422 records nothing: the used-hash ledger is written only on a 200, so after a refused delivery the
  same `block_hash` can be sent again with the deliverable as text or `deliverable_b64`.
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
