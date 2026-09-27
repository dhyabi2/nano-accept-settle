"""Nano addresses and state-block hashing, stdlib only (hashlib.blake2b)."""
import hashlib

_ALPHABET = "13456789abcdefghijkmnopqrstuwxyz"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}


class AddressError(ValueError):
    pass


def _b32decode(s):
    n = 0
    for c in s:
        if c not in _INDEX:
            raise AddressError("invalid character %r" % c)
        n = (n << 5) | _INDEX[c]
    return n


def _b32encode(n, chars):
    out = []
    for _ in range(chars):
        out.append(_ALPHABET[n & 31])
        n >>= 5
    return "".join(reversed(out))


def address_to_pubkey(address):
    """Return the 32-byte public key of a nano_/xrb_ address, checking its checksum."""
    if not isinstance(address, str):
        raise AddressError("address must be a string")
    for prefix in ("nano_", "xrb_"):
        if address.startswith(prefix):
            body = address[len(prefix):]
            break
    else:
        raise AddressError("address must start with nano_ or xrb_")
    if len(body) != 60:
        raise AddressError("address body must be 60 characters")
    key = _b32decode(body[:52])
    if key >> 256:
        raise AddressError("public key out of range")
    pub = key.to_bytes(32, "big")
    check = _b32decode(body[52:]).to_bytes(5, "big")
    want = hashlib.blake2b(pub, digest_size=5).digest()[::-1]
    if check != want:
        raise AddressError("checksum mismatch")
    return pub


def pubkey_to_address(pub):
    if len(pub) != 32:
        raise AddressError("public key must be 32 bytes")
    check = hashlib.blake2b(pub, digest_size=5).digest()[::-1]
    return "nano_" + _b32encode(int.from_bytes(pub, "big"), 52) + _b32encode(int.from_bytes(check, "big"), 8)


def normalize_address(address):
    return pubkey_to_address(address_to_pubkey(address))


def _hex32(value, name):
    try:
        b = bytes.fromhex(value)
    except (TypeError, ValueError):
        raise ValueError("%s is not hex" % name)
    if len(b) != 32:
        raise ValueError("%s must be 32 bytes" % name)
    return b


def state_block_hash(contents):
    """Hash of a state block, computed locally from its fields (so the RPC cannot swap blocks)."""
    preamble = (6).to_bytes(32, "big")
    balance = int(contents["balance"])
    if not 0 <= balance < 1 << 128:
        raise ValueError("balance out of range")
    data = (
        preamble
        + address_to_pubkey(contents["account"])
        + _hex32(contents["previous"], "previous")
        + address_to_pubkey(contents["representative"])
        + balance.to_bytes(16, "big")
        + _hex32(contents["link"], "link")
    )
    return hashlib.blake2b(data, digest_size=32).hexdigest().upper()
