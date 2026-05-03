"""Payout signer runner.

Background loop:
  1. Find QUEUED PendingPayouts (oldest first, bounded batch size)
  2. For each: mark SIGNING, hand to PayoutSigner.sign_and_submit()
  3. On success: status=SUBMITTED, store tx_hash
  4. On failure: increment attempts, log error, return to QUEUED with backoff
                 (or mark FAILED after MAX_ATTEMPTS)

The actual on-chain confirmation watch (SUBMITTED → CONFIRMED) lives in the
chain watcher — when a block arrives, watcher checks any pending tx_hashes.
This runner only handles the QUEUED → SUBMITTED transition.

Phase C ships with NullSigner so QUEUED rows pile up with a warning rather
than fail noisily. Swap signer in main.py once you've decided on key custody.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import PendingPayout
from src.db.session import session_scope
from src.signing.queue import NullSigner, PayoutSigner

log = structlog.get_logger(__name__)

MAX_ATTEMPTS = 5
BATCH_SIZE = 10
LOOP_INTERVAL_SECONDS = 30


class SignerRunner:
    def __init__(self, signer: PayoutSigner | None = None) -> None:
        self.signer = signer or NullSigner()
        self._stop = asyncio.Event()

    async def loop_forever(self) -> None:
        log.info("signer_runner_starting", signer_class=type(self.signer).__name__)
        while not self._stop.is_set():
            try:
                processed = await self._tick()
                if processed == 0:
                    # nothing to do — sleep and retry
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=LOOP_INTERVAL_SECONDS
                    )
            except asyncio.TimeoutError:
                continue  # normal sleep wakeup
            except Exception as e:  # noqa: BLE001
                log.error("signer_runner_tick_failed", error=str(e), exc_info=True)
                await asyncio.sleep(LOOP_INTERVAL_SECONDS)

    async def _tick(self) -> int:
        async with session_scope() as session:
            payouts = await self._claim_batch(session)

            if not payouts:
                return 0

            log.info("signer_processing_batch", count=len(payouts))

        # Process each outside the original transaction so failures don't
        # roll back the SIGNING claim.
        for payout_id in [p.id for p in payouts]:
            await self._process_one(payout_id)

        return len(payouts)

    async def _claim_batch(self, session: AsyncSession) -> list[PendingPayout]:
        """Atomically transition up to BATCH_SIZE rows from QUEUED → SIGNING."""
        # Find candidates
        q = (
            select(PendingPayout)
            .where(PendingPayout.status == "QUEUED")
            .where(PendingPayout.attempts < MAX_ATTEMPTS)
            .order_by(PendingPayout.created_at)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(q)
        payouts = list(result.scalars())

        for p in payouts:
            p.status = "SIGNING"

        await session.commit()
        return payouts

    async def _process_one(self, payout_id: int) -> None:
        async with session_scope() as session:
            payout = await session.get(PendingPayout, payout_id)
            if payout is None or payout.status != "SIGNING":
                return

            try:
                tx_hash = await self.signer.sign_and_submit(payout)
                payout.status = "SUBMITTED"
                payout.tx_hash = tx_hash
                payout.attempts += 1
                log.info(
                    "payout_submitted",
                    payout_id=payout_id,
                    tx_hash=tx_hash,
                    recipient=payout.recipient,
                    amount=payout.amount,
                )
            except NotImplementedError as e:
                # NullSigner — return to QUEUED so a real signer can pick up later.
                payout.status = "QUEUED"
                payout.attempts += 1
                payout.error_message = f"signer_not_wired: {e}"[:512]
                log.warning(
                    "payout_returned_to_queue_null_signer",
                    payout_id=payout_id,
                    attempts=payout.attempts,
                )
            except Exception as e:  # noqa: BLE001
                payout.attempts += 1
                payout.error_message = str(e)[:512]
                if payout.attempts >= MAX_ATTEMPTS:
                    payout.status = "FAILED"
                    log.error(
                        "payout_failed_terminal",
                        payout_id=payout_id,
                        attempts=payout.attempts,
                        error=str(e),
                    )
                else:
                    payout.status = "QUEUED"
                    log.warning(
                        "payout_retry_queued",
                        payout_id=payout_id,
                        attempts=payout.attempts,
                        error=str(e),
                    )

    def stop(self) -> None:
        self._stop.set()
