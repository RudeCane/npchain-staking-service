"""Payout queue + signer interface.

Two pieces:
  1. enqueue_payout() — called by API write handlers to add a payout to the queue
  2. PayoutSigner protocol — the swappable interface for actual signing

Phase C ships with PayoutSigner = NullSigner (records intent, doesn't sign or
submit). Phases C+ swap in real implementations:

  - HotKeySigner: foundation operations key on disk, encrypted with KDF passphrase
  - HSMSigner: defers signing to a hardware module (YubiHSM, Ledger CLI, etc.)
  - MultisigCoordinator: coordinates 2-of-3 across multiple signers

The runner loop (signing/runner.py) picks up QUEUED rows, calls signer.sign_and_submit(),
updates status accordingly.
"""

from __future__ import annotations

from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import PendingPayout

log = structlog.get_logger(__name__)


class PayoutSigner(Protocol):
    """Anything that can take a PendingPayout and turn it into an on-chain tx.

    Implementations are responsible for:
      - Building the TRANSFER tx
      - Signing with whatever key custody model they wrap
      - Submitting to L1 RPC
      - Returning the tx_hash on success, or raising on failure
    """

    async def sign_and_submit(self, payout: PendingPayout) -> str: ...


class NullSigner:
    """Default Phase A/B signer: doesn't sign, doesn't submit. Just logs.

    Use case: develop + test the API surface without committing to a key
    management strategy yet. Payouts pile up in QUEUED status and an alert
    fires if the queue grows large.
    """

    async def sign_and_submit(self, payout: PendingPayout) -> str:
        log.warning(
            "NULL_SIGNER_payout_pending",
            payout_id=payout.id,
            recipient=payout.recipient,
            amount=payout.amount,
            kind=payout.kind,
            note="Plug in a real signer (HotKeySigner / HSMSigner / MultisigCoordinator).",
        )
        raise NotImplementedError("NullSigner cannot actually sign — Phase C wiring required")


async def enqueue_payout(
    session: AsyncSession,
    recipient: str,
    amount: int,
    kind: str,
    ref: str,
) -> int:
    """Insert a PendingPayout row. Idempotent on `ref`.

    Returns the payout id. If the ref already exists, returns the existing id
    (swallow IntegrityError) — this makes retry-safe write paths trivial.
    """
    if amount <= 0:
        raise ValueError(f"payout_amount_must_be_positive (got {amount})")
    if kind != "UNSTAKE_REFUND":
        raise ValueError(f"unknown_payout_kind: {kind} (only UNSTAKE_REFUND is supported)")

    payout = PendingPayout(
        recipient=recipient,
        amount=amount,
        kind=kind,
        ref=ref,
        status="QUEUED",
    )
    session.add(payout)
    try:
        await session.flush()
        log.info(
            "payout_enqueued",
            payout_id=payout.id,
            recipient=recipient,
            amount=amount,
            kind=kind,
            ref=ref,
        )
        return payout.id
    except IntegrityError:
        # ref already exists — fetch and return existing id
        await session.rollback()
        existing = await session.execute(
            select(PendingPayout).where(PendingPayout.ref == ref)
        )
        existing_payout = existing.scalar_one_or_none()
        if existing_payout is None:
            raise
        log.info(
            "payout_already_queued",
            payout_id=existing_payout.id,
            ref=ref,
            note="Idempotent retry — returning existing id",
        )
        return existing_payout.id
