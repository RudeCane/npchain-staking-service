"""Write API: request unstake, claim rewards, cancel unstake.

These endpoints CHANGE staker state, so every request requires a Dilithium
signature over a canonical message. Replay protection via nonce + timestamp.

Actual on-chain TRANSFERs (foundation operations wallet → user) are queued
into PendingPayout records — the signing service (Phase C) consumes those
and submits to L1.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import CancelUnstake, RequestUnstake
from src.config import settings
from src.db.models import Staker, UnstakeRequest, WatcherState
from src.db.session import get_session
from src.signing.queue import enqueue_payout
from src.staking.auth import SignatureError, verify_signed_request
from src.staking.tiers import tier_for

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/stake", tags=["stake-write"])


async def _current_height(session: AsyncSession) -> int:
    state = await session.get(WatcherState, 1)
    if state is None:
        raise HTTPException(status_code=503, detail="service_uninitialized")
    if not state.snapshot_complete:
        raise HTTPException(status_code=503, detail="snapshot_in_progress")
    return state.last_processed_height


@router.post("/request-unstake")
async def request_unstake(
    req: RequestUnstake, session: AsyncSession = Depends(get_session)
) -> dict:
    try:
        await verify_signed_request(
            action="UNSTAKE",
            address=req.address,
            amount=req.amount,
            nonce=req.nonce,
            timestamp=req.timestamp,
            signature=req.signature,
            pubkey=req.pubkey,
        )
    except SignatureError as e:
        raise HTTPException(status_code=401, detail=f"signature_error: {e}") from e

    staker = await session.get(Staker, req.address)
    if staker is None:
        raise HTTPException(status_code=404, detail="staker_not_found")

    if staker.staked_amount < req.amount:
        raise HTTPException(
            status_code=400,
            detail=f"insufficient_stake (have {staker.staked_amount}, requested {req.amount})",
        )

    height = await _current_height(session)
    unlock_height = height + settings.unstake_cooldown_blocks

    # Lock funds: decrement staked_amount immediately, create pending request
    new_staked = staker.staked_amount - req.amount
    staker.staked_amount = new_staked
    new_tier = tier_for(new_staked)
    staker.tier = new_tier.name
    staker.last_activity_height = height

    unstake_req = UnstakeRequest(
        address=req.address,
        amount=req.amount,
        request_height=height,
        unlock_height=unlock_height,
        status="PENDING",
    )
    session.add(unstake_req)
    await session.commit()
    await session.refresh(unstake_req)

    log.info(
        "unstake_requested",
        address=req.address,
        amount=req.amount,
        unlock_height=unlock_height,
        request_id=unstake_req.id,
    )

    return {
        "status": "queued",
        "request_id": unstake_req.id,
        "amount": req.amount,
        "amount_npc": f"{req.amount / 100_000_000:.4f}",
        "request_height": height,
        "unlock_height": unlock_height,
        "blocks_until_unlock": unlock_height - height,
        "new_staked_amount": new_staked,
        "new_tier": new_tier.name,
    }


@router.post("/cancel-unstake")
async def cancel_unstake(
    req: CancelUnstake, session: AsyncSession = Depends(get_session)
) -> dict:
    try:
        # cancel doesn't have an `amount`, but the verifier expects one.
        # Use 0 — the canonical message format covers it.
        await verify_signed_request(
            action="CANCEL_UNSTAKE",
            address=req.address,
            amount=req.request_id,  # request_id stands in for amount in canonical msg
            nonce=req.nonce,
            timestamp=req.timestamp,
            signature=req.signature,
            pubkey=req.pubkey,
        )
    except SignatureError as e:
        raise HTTPException(status_code=401, detail=f"signature_error: {e}") from e

    unstake_req = await session.get(UnstakeRequest, req.request_id)
    if unstake_req is None:
        raise HTTPException(status_code=404, detail="request_not_found")

    if unstake_req.address != req.address:
        raise HTTPException(status_code=403, detail="not_your_request")

    if unstake_req.status not in ("PENDING", "READY"):
        raise HTTPException(
            status_code=400, detail=f"cannot_cancel (status={unstake_req.status})"
        )

    staker = await session.get(Staker, req.address)
    if staker is None:
        raise HTTPException(status_code=404, detail="staker_not_found")

    height = await _current_height(session)

    # Restore the locked amount
    new_staked = staker.staked_amount + unstake_req.amount
    staker.staked_amount = new_staked
    new_tier = tier_for(new_staked)
    staker.tier = new_tier.name
    staker.last_activity_height = height

    unstake_req.status = "CANCELLED"
    unstake_req.completed_height = height

    await session.commit()

    log.info(
        "unstake_cancelled",
        address=req.address,
        request_id=req.request_id,
        amount=unstake_req.amount,
        new_staked_amount=new_staked,
    )

    return {
        "status": "cancelled",
        "request_id": req.request_id,
        "amount_restored": unstake_req.amount,
        "amount_restored_npc": f"{unstake_req.amount / 100_000_000:.4f}",
        "new_staked_amount": new_staked,
        "new_tier": new_tier.name,
    }
