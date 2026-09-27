"""nano-accept-settle: confirm an answer passed its test and the asker's XNO payment settled, in one call.

Non-custodial. Reads the public Nano ledger; never holds, signs or moves funds.
"""
from .core import (  # noqa: F401
    DEFAULT_RPC, RAW_PER_XNO, Deal, DealError, Rpc, RpcError, UsedHashLedger,
    accept_and_settle, check_acceptance, read_send, verify_payment,
)

__version__ = "0.1.0"
