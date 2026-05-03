"""Pydantic schemas for write API requests.

All write operations require a Dilithium signature over a canonical message.
The message format mirrors how the wallet builds it:

    msg = f"{action}|{address}|{amount}|{nonce}|{timestamp}"
    sig = dilithium3_sign(privkey, msg)

Service receives:
    {
        "address": "NPC...",
        "amount": 12345678901234,    # base units
        "nonce": "uuid-v4-here",
        "timestamp": 1714512000,
        "signature": "hex-encoded-dilithium3-signature",
        "pubkey": "hex-encoded-dilithium3-pubkey"
    }

Signature verification rules:
    1. timestamp must be within +/- 300 seconds of server time (anti-replay)
    2. nonce must not have been seen before (anti-replay double-submit)
    3. dilithium3.verify(pubkey, msg, sig) must return true
    4. derive_address(pubkey) must equal `address`
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignedRequestBase(BaseModel):
    """Common fields for any signed write request."""

    address: str = Field(..., min_length=43, max_length=64)
    amount: int = Field(..., gt=0)
    nonce: str = Field(..., min_length=8, max_length=64)
    timestamp: int = Field(..., gt=0)
    signature: str = Field(..., min_length=10)
    pubkey: str = Field(..., min_length=10)


class RequestUnstake(SignedRequestBase):
    """User requests to unstake `amount` from their staked balance.

    Service:
      1. Verifies signature
      2. Confirms staker has at least `amount` staked
      3. Creates UnstakeRequest with unlock_height = current + cooldown
      4. Decrements staker.staked_amount immediately (locks the funds in
         pending state — they're no longer counted for tier or rewards)
    """

    pass


class CancelUnstake(BaseModel):
    """User cancels a pending unstake request before unlock_height.

    Returns the locked amount to staked_amount (re-counts for tier + rewards).
    """

    address: str = Field(..., min_length=43, max_length=64)
    request_id: int = Field(..., gt=0)
    nonce: str = Field(..., min_length=8, max_length=64)
    timestamp: int = Field(..., gt=0)
    signature: str = Field(..., min_length=10)
    pubkey: str = Field(..., min_length=10)
