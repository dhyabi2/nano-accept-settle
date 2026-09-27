"""Accept an answer and confirm its XNO payment settled, in one call. Non-custodial: read-only."""
import datetime
import hashlib
import json
import os
import re
import sqlite3
import threading
import urllib.request

from .nano import AddressError, normalize_address, pubkey_to_address, state_block_hash

__all__ = [
    "Deal", "DealError", "Rpc", "UsedHashLedger", "check_acceptance", "verify_payment",
    "accept_and_settle", "RAW_PER_XNO", "DEFAULT_RPC",
]

RAW_PER_XNO = 10 ** 30
DEFAULT_RPC = "https://rpc.nano.to"
USER_AGENT = "nano-accept-settle/0.1 (+https://github.com/dhyabi2/nano-accept-settle)"
MAX_DELIVERABLE_BYTES = 1_000_000
MAX_PATTERN_CHARS = 512
_HASH_RE = re.compile(r"^[0-9A-Fa-f]{64}$")


class DealError(ValueError):
    pass


def _parse_deadline(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise DealError("deadline must be ISO 8601 or unix seconds")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.isdigit():
            return int(value)
        try:
            dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise DealError("deadline must be ISO 8601 or unix seconds")
        if dt.tzinfo is None:
            raise DealError("deadline needs a timezone (use Z for UTC)")
        return int(dt.timestamp())
    raise DealError("deadline must be ISO 8601 or unix seconds")


def _check_acceptance_spec(spec):
    if not isinstance(spec, dict):
        raise DealError("acceptance must be an object")
    kind = spec.get("type")
    if kind == "sha256":
        if not isinstance(spec.get("sha256"), str) or not _HASH_RE.match(spec["sha256"]):
            raise DealError("acceptance.sha256 must be 64 hex characters")
    elif kind == "required_keys":
        keys = spec.get("required")
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k for k in keys):
            raise DealError("acceptance.required must be a non-empty list of key names")
        types = spec.get("types", {})
        if not isinstance(types, dict) or any(t not in _JSON_TYPES for t in types.values()):
            raise DealError("acceptance.types values must be one of %s" % sorted(_JSON_TYPES))
    elif kind == "regex":
        pattern = spec.get("pattern")
        if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_CHARS:
            raise DealError("acceptance.pattern must be 1..%d characters" % MAX_PATTERN_CHARS)
        try:
            re.compile(pattern, _flags(spec.get("flags", "")))
        except re.error as e:
            raise DealError("acceptance.pattern does not compile: %s" % e)
    else:
        raise DealError("acceptance.type must be sha256, required_keys or regex")


def _flags(text):
    flags = 0
    for ch in text or "":
        if ch == "i":
            flags |= re.IGNORECASE
        elif ch == "m":
            flags |= re.MULTILINE
        elif ch == "s":
            flags |= re.DOTALL
        else:
            raise DealError("acceptance.flags may only contain i, m, s")
    return flags


_JSON_TYPES = {
    "string": (str,), "number": (int, float), "integer": (int,), "boolean": (bool,),
    "object": (dict,), "array": (list,), "null": (type(None),),
}


class Deal:
    """The terms both sides agreed to. See SCHEMA.md for the pinned field list."""

    FIELDS = ("asker", "answerer", "amount_raw", "deadline", "acceptance", "what_it_buys")

    def __init__(self, asker, answerer, amount_raw, acceptance, deadline=None, what_it_buys=None):
        try:
            self.asker = normalize_address(asker)
            self.answerer = normalize_address(answerer)
        except AddressError as e:
            raise DealError("bad address: %s" % e)
        if self.asker == self.answerer:
            raise DealError("asker and answerer must differ")
        if not isinstance(amount_raw, str) or not amount_raw.isdigit() or amount_raw.startswith("0"):
            raise DealError("amount_raw must be a positive integer string of raw (1 XNO = 10**30 raw)")
        if int(amount_raw) >= 1 << 128:
            raise DealError("amount_raw out of range")
        self.amount_raw = amount_raw
        _check_acceptance_spec(acceptance)
        self.acceptance = acceptance
        self.deadline = _parse_deadline(deadline)
        if what_it_buys is not None and not isinstance(what_it_buys, str):
            raise DealError("what_it_buys must be a string")
        self.what_it_buys = what_it_buys

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            raise DealError("deal must be an object")
        unknown = set(d) - set(cls.FIELDS) - {"deal_id", "amount_xno"}
        if unknown:
            raise DealError("unknown deal fields: %s" % ", ".join(sorted(unknown)))
        missing = [k for k in ("asker", "answerer", "amount_raw", "acceptance") if k not in d]
        if missing:
            raise DealError("missing deal fields: %s" % ", ".join(missing))
        deal = cls(d["asker"], d["answerer"], d["amount_raw"], d["acceptance"],
                   d.get("deadline"), d.get("what_it_buys"))
        if "deal_id" in d and d["deal_id"] != deal.deal_id:
            raise DealError("deal_id does not match the terms")
        return deal

    def to_dict(self):
        out = {
            "asker": self.asker, "answerer": self.answerer, "amount_raw": self.amount_raw,
            "acceptance": self.acceptance, "deadline": self.deadline,
        }
        if self.what_it_buys is not None:
            out["what_it_buys"] = self.what_it_buys
        return out

    @property
    def deal_id(self):
        canon = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode()).hexdigest()

    def terms(self):
        """What a 402 response carries: everything a payer needs, with the XNO amount as an exact decimal string."""
        d = self.to_dict()
        d["deal_id"] = self.deal_id
        whole, frac = divmod(int(self.amount_raw), RAW_PER_XNO)
        d["amount_xno"] = str(whole) + ("." + str(frac).rjust(30, "0").rstrip("0") if frac else "")
        d["pay_to"] = self.answerer
        return d


def _as_bytes(deliverable):
    if isinstance(deliverable, bytes):
        return deliverable
    if isinstance(deliverable, str):
        return deliverable.encode("utf-8")
    return json.dumps(deliverable, sort_keys=True, separators=(",", ":")).encode()


def check_acceptance(deal, deliverable):
    """Run the deal's acceptance test. Returns {"passed": bool, "reasons": [...]}."""
    reasons = []
    try:
        data = _as_bytes(deliverable)
    except (TypeError, ValueError):
        return {"passed": False, "reasons": ["deliverable is not bytes, text or JSON"]}
    if len(data) > MAX_DELIVERABLE_BYTES:
        return {"passed": False, "reasons": ["deliverable larger than %d bytes" % MAX_DELIVERABLE_BYTES]}
    spec = deal.acceptance
    kind = spec["type"]
    if kind == "sha256":
        got = hashlib.sha256(data).hexdigest()
        if got != spec["sha256"].lower():
            reasons.append("sha256 mismatch: got %s" % got)
    elif kind == "required_keys":
        if isinstance(deliverable, (dict, list)):
            obj = deliverable
        else:
            try:
                obj = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return {"passed": False, "reasons": ["deliverable is not JSON"]}
        if not isinstance(obj, dict):
            return {"passed": False, "reasons": ["deliverable is not a JSON object"]}
        for key in spec["required"]:
            if key not in obj:
                reasons.append("missing key: %s" % key)
        for key, tname in spec.get("types", {}).items():
            if key in obj:
                v = obj[key]
                ok = isinstance(v, _JSON_TYPES[tname]) and not (isinstance(v, bool) and tname in ("number", "integer"))
                if not ok:
                    reasons.append("key %s is not %s" % (key, tname))
    elif kind == "regex":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {"passed": False, "reasons": ["deliverable is not UTF-8 text"]}
        rx = re.compile(spec["pattern"], _flags(spec.get("flags", "")))
        m = rx.fullmatch(text) if spec.get("fullmatch") else rx.search(text)
        if not m:
            reasons.append("regex did not match")
    return {"passed": not reasons, "reasons": reasons}


class RpcError(RuntimeError):
    pass


class Rpc:
    """Minimal read-only Nano RPC client. Only block_info is ever called."""

    def __init__(self, url=DEFAULT_RPC, timeout=15):
        self.url = url
        self.timeout = timeout

    def __call__(self, payload):
        if payload.get("action") != "block_info":
            raise RpcError("this client only reads block_info")
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read(1_000_000)
        except Exception as e:  # network, TLS, HTTP errors
            raise RpcError("rpc request failed: %s" % e)
        try:
            return json.loads(body)
        except ValueError:
            raise RpcError("rpc answered non-JSON")


def _block_info(rpc, block_hash):
    info = rpc({"action": "block_info", "json_block": "true", "hash": block_hash})
    if not isinstance(info, dict):
        raise RpcError("rpc answered a non-object")
    if "error" in info:
        raise RpcError("rpc error: %s" % info["error"])
    return info


def read_send(block_hash, rpc):
    """Read a block and derive, without trusting the RPC's summary fields, who sent how much to whom.

    The block hash is recomputed from the returned contents, and the amount is the drop in balance
    from the previous block (also hash-checked). Confirmation status is the one thing taken from the RPC.
    """
    info = _block_info(rpc, block_hash)
    c = info.get("contents")
    if not isinstance(c, dict) or c.get("type") != "state":
        raise RpcError("not a state block")
    if state_block_hash(c) != block_hash.upper():
        raise RpcError("rpc returned contents that do not hash to the requested block")
    balance = int(c["balance"])
    if c["previous"] == "0" * 64:
        prev_balance = 0
    else:
        prev = _block_info(rpc, c["previous"])
        pc = prev.get("contents")
        if not isinstance(pc, dict) or pc.get("type") != "state" or state_block_hash(pc) != c["previous"].upper():
            raise RpcError("previous block could not be verified")
        prev_balance = int(pc["balance"])
    delta = prev_balance - balance
    return {
        "hash": block_hash.upper(),
        "account": normalize_address(c["account"]),
        "is_send": delta > 0,
        "amount_raw": str(delta) if delta > 0 else "0",
        "destination": pubkey_to_address(bytes.fromhex(c["link"])) if delta > 0 else None,
        "confirmed": str(info.get("confirmed")).lower() == "true",
        "local_timestamp": int(info.get("local_timestamp") or 0),
        "rpc_amount_raw": info.get("amount"),
        "rpc_subtype": info.get("subtype"),
    }


class UsedHashLedger:
    """A small local SQLite record of which block paid which deal, so one payment settles one deal."""

    def __init__(self, path=None):
        self.path = path or os.environ.get("NANO_ACCEPT_SETTLE_DB") or os.path.join(
            os.path.expanduser("~"), ".nano-accept-settle", "used.db")
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("CREATE TABLE IF NOT EXISTS used (block TEXT PRIMARY KEY, deal_id TEXT NOT NULL, at INTEGER NOT NULL)")

    def owner(self, block_hash):
        with self._lock:
            row = self._db.execute("SELECT deal_id FROM used WHERE block=?", (block_hash.upper(),)).fetchone()
        return row[0] if row else None

    def claim(self, block_hash, deal_id):
        """Record block -> deal atomically. Returns the deal that owns the block afterwards."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT deal_id FROM used WHERE block=?", (block_hash.upper(),)).fetchone()
                if row is None:
                    self._db.execute("INSERT INTO used VALUES (?,?,strftime('%s','now'))", (block_hash.upper(), deal_id))
                    owner = deal_id
                else:
                    owner = row[0]
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return owner


def verify_payment(deal, block_hash, rpc=None, ledger=None):
    """Confirm block_hash is a confirmed send from deal.asker to deal.answerer of exactly deal.amount_raw.

    Returns {"paid": bool, "reused": bool, "block": {...} or None, "reasons": [...]}.
    Does not record the hash; accept_and_settle does that only when everything passes.
    """
    rpc = rpc or Rpc()
    out = {"paid": False, "reused": False, "block": None, "reasons": [], "rpc_error": False}
    if not isinstance(block_hash, str) or not _HASH_RE.match(block_hash):
        out["reasons"].append("block hash must be 64 hex characters")
        return out
    if ledger is not None:
        owner = ledger.owner(block_hash)
        if owner is not None and owner != deal.deal_id:
            out["reused"] = True
            out["reasons"].append("block already settled another deal")
            return out
    try:
        blk = read_send(block_hash, rpc)
    except (RpcError, KeyError, ValueError, AddressError) as e:
        out["rpc_error"] = isinstance(e, RpcError)
        out["reasons"].append(str(e) or e.__class__.__name__)
        return out
    out["block"] = blk
    r = out["reasons"]
    if not blk["is_send"]:
        r.append("block is not a send")
    if blk["account"] != deal.asker:
        r.append("sender is %s, not the asker" % blk["account"])
    if blk["destination"] != deal.answerer:
        r.append("destination is %s, not the answerer" % blk["destination"])
    if blk["amount_raw"] != deal.amount_raw:
        r.append("amount is %s raw, deal says %s raw" % (blk["amount_raw"], deal.amount_raw))
    if not blk["confirmed"]:
        r.append("block is not confirmed yet")
    out["paid"] = not r
    blk["on_time"] = None if not (deal.deadline and blk["local_timestamp"]) else blk["local_timestamp"] <= deal.deadline
    return out


def accept_and_settle(deal, deliverable, block_hash, rpc=None, ledger=None):
    """One call: did the answer pass its test, and has the asker's payment settled?

    Returns a receipt with an HTTP-style status:
      200 accepted and paid; 402 accepted but not (yet) paid, with the deal terms;
      409 the block already settled a different deal; 422 the acceptance test failed;
      503 the RPC could not be read (try again, nothing was recorded).
    """
    if ledger is None:
        ledger = UsedHashLedger()
    acc = check_acceptance(deal, deliverable)
    receipt = {
        "deal_id": deal.deal_id, "accepted": acc["passed"], "paid": False,
        "block": block_hash.upper() if isinstance(block_hash, str) else None,
        "reasons": list(acc["reasons"]),
    }
    if not acc["passed"]:
        receipt["status"] = 422
        return receipt
    pay = verify_payment(deal, block_hash, rpc=rpc, ledger=ledger)
    receipt["reasons"] += pay["reasons"]
    receipt["payment"] = pay["block"]
    if pay["reused"]:
        receipt["status"] = 409
        return receipt
    if pay["rpc_error"]:
        receipt["status"] = 503
        return receipt
    if not pay["paid"]:
        receipt["status"] = 402
        receipt["terms"] = deal.terms()
        return receipt
    if ledger.claim(block_hash, deal.deal_id) != deal.deal_id:
        receipt["status"] = 409
        receipt["reasons"].append("block already settled another deal")
        return receipt
    receipt["paid"] = True
    receipt["status"] = 200
    return receipt
