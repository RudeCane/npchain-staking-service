"""Unstake-cooldown processor.

Walks PENDING UnstakeRequests where unlock_height <= current_height.
For each, transitions to READY and enqueues a UNSTAKE_REFUND payout.

Runs as part of the chain watcher loop — every block, check for unlocks.
This keeps the cooldown logic deterministic (block-based, not wall-clock).
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import UnstakeRequest
from src.signing.queue import enqueue_payout

log = structlog.get_logger(__name__)


async def process_unlocked_unstakes(
    session: AsyncSession, current_height: int
) -> int:
    """Find PENDING unstakes whose cooldown has expired, queue refunds.

    Returns count of unstakes transitioned this call.
    """
    q = (
        select(UnstakeRequest)
        .where(UnstakeRequest.status == "PENDING")
        .where(UnstakeRequest.unlock_height <= current_height)
    )
    result = await session.execute(q)
    unlocked = list(result.scalars())

    if not unlocked:
        return 0

    log.info("processing_unlocked_unstakes", count=len(unlocked), height=current_height)

    for u in unlocked:
        u.status = "READY"
        try:
            payout_id = await enqueue_payout(
                session=session,
                recipient=u.address,
                amount=u.amount,
                kind="UNSTAKE_REFUND",
                ref=f"unstake:{u.id}",
            )
            log.info(
                "unstake_refund_queued",
                unstake_request_id=u.id,
                payout_id=payout_id,
                recipient=u.address,
                amount=u.amount,
            )
        except Exception as e:  # noqa: BLE001
            # Leave the unstake in READY status — next tick will retry queueing
            log.error(
                "unstake_refund_enqueue_failed",
                unstake_request_id=u.id,
                error=str(e),
            )

    return len(unlocked)
