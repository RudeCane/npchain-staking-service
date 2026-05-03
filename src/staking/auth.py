"""Signature verification + replay protection for write API.

Dilithium3 signature verification in Python is not built-in. Two paths:

  Option A: Use the pq-crystals reference C library via cffi/ctypes
  Option B: Shell out to a small npchain CLI tool that wraps the existing
            C++ dilithium3 module
  Option C: Reuse pyspx or similar (does NOT support Dilithium — only SPHINCS+)

For Phase B, this module STUBS the signature check (accepts any non-empty
signature) so the API surface can be developed and tested against. Phase C
swaps in real verification by wiring to one of options A or B above.

The replay-protection logic (nonce + timestamp) IS real and works today.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings

log = structlog.get_logger(__name__)

# Anti-replay window: timestamps must be within +/- this many seconds of server time
TIMESTAMP_WINDOW_SECONDS = 300

# In-memory nonce cache. For multi-instance deployment, swap for Redis or
# a DB-backed nonce store with TTL cleanup.
_seen_nonces: dict[str, float] = {}


class SignatureError(Exception):
    """Raised when signature verification or replay protection fails."""

    pass


def _purge_old_nonces() -> None:
    """Drop nonces older than the timestamp window — they can't be replayed."""
    now = time.time()
    cutoff = now - TIMESTAMP_WINDOW_SECONDS - 60  # extra grace
    stale = [n for n, t in _seen_nonces.items() if t < cutoff]
    for n in stale:
        _seen_nonces.pop(n, None)


def _check_replay(nonce: str, timestamp: int) -> None:
    """Raise SignatureError if request looks replayed."""
    now = int(time.time())
    if abs(now - timestamp) > TIMESTAMP_WINDOW_SECONDS:
        raise SignatureError(
            f"timestamp_outside_window (got {timestamp}, server now {now}, max drift {TIMESTAMP_WINDOW_SECONDS}s)"
        )

    _purge_old_nonces()
    if nonce in _seen_nonces:
        raise SignatureError("nonce_already_used")

    _seen_nonces[nonce] = float(now)


def _canonical_message(action: str, address: str, amount: int, nonce: str, timestamp: int) -> bytes:
    """Build the exact byte-string that wallet signed.

    Wallet must use this same format. Any deviation → signature won't verify.
    """
    msg = f"{action}|{address}|{amount}|{nonce}|{timestamp}"
    return msg.encode("utf-8")


def _verify_dilithium_stub(pubkey_hex: str, message: bytes, signature_hex: str) -> bool:
    """STUB. Phase C replaces this with real Dilithium3.verify().

    Phase B: returns True if signature is non-empty hex. Lets the API surface
    be exercised end-to-end without the real verifier wired up.

    DO NOT deploy this to production with the stub still in place.
    A clear log warning is emitted so this can't slip past review.
    """
    log.warning(
        "DILITHIUM_VERIFY_STUBBED",
        pubkey_prefix=pubkey_hex[:16],
        sig_prefix=signature_hex[:16],
        msg_len=len(message),
        note="Phase C wiring required before production",
    )
    if not signature_hex or len(signature_hex) < 10:
        return False
    if not pubkey_hex or len(pubkey_hex) < 10:
        return False
    try:
        bytes.fromhex(signature_hex)
        bytes.fromhex(pubkey_hex)
    except ValueError:
        return False
    return True  # STUBBED — replace in Phase C


def _derive_address_stub(pubkey_hex: str) -> str:
    """STUB. Phase C replaces with real address derivation.

    Real derivation per NPChain: address = "NPC" + sha3_256(pubkey)[:20]_hex
    Stub: returns input as-is (skips the pubkey↔address binding check).
    """
    return ""  # Stub returns empty so the address check below is permissive


async def verify_signed_request(
    action: str,
    address: str,
    amount: int,
    nonce: str,
    timestamp: int,
    signature: str,
    pubkey: str,
) -> None:
    """Verify a signed write request. Raises SignatureError on any failure.

    Steps:
      1. Replay protection (nonce + timestamp window)
      2. Dilithium signature verification
      3. Pubkey derives to claimed address

    Returns None on success.
    """
    _check_replay(nonce, timestamp)

    msg = _canonical_message(action, address, amount, nonce, timestamp)

    if not _verify_dilithium_stub(pubkey, msg, signature):
        raise SignatureError("signature_invalid")

    derived = _derive_address_stub(pubkey)
    if derived and derived != address:
        # Stub returns "" so this branch only triggers once Phase C lands
        raise SignatureError(f"address_mismatch (pubkey derives to {derived})")
