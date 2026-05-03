"""Stake info endpoints. Read-only in Phase A.

Routes:
    GET /api/v1/stake/{addr}         — single staker info
    GET /api/v1/leaderboard          — top stakers by stake amount
    GET /api/v1/health               — service health + watcher status
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import RewardPoolInflow, Staker, UnstakeRequest, ValidatorReward, WatcherState
from src.db.session import get_session
from src.migrations import canonicalize

router = APIRouter(prefix="/api/v1", tags=["stake"])


def _to_npc(base_units: int) -> str:
    """Format base units as NPC string with 4 decimals."""
    return f"{base_units / 100_000_000:.4f}"


@router.get("/stake/{address}")
async def get_stake(address: str, session: AsyncSession = Depends(get_session)) -> dict:
    canonical = canonicalize(address)
    staker = await session.get(Staker, canonical)

    if staker is None:
        return {
            "address": canonical,
            "input_address": address,
            "staked_amount": 0,
            "staked_amount_npc": "0.0000",
            "tier": "None",
            "first_stake_height": None,
            "last_activity_height": None,
            "pending_unstakes": [],
        }

    # pending unstake requests for this address
    pq = await session.execute(
        select(UnstakeRequest)
        .where(UnstakeRequest.address == canonical)
        .where(UnstakeRequest.status.in_(("PENDING", "READY")))
        .order_by(UnstakeRequest.unlock_height)
    )
    pending = [
        {
            "id": u.id,
            "amount": u.amount,
            "amount_npc": _to_npc(u.amount),
            "request_height": u.request_height,
            "unlock_height": u.unlock_height,
            "status": u.status,
        }
        for u in pq.scalars()
    ]

    return {
        "address": canonical,
        "input_address": address,
        "staked_amount": staker.staked_amount,
        "staked_amount_npc": _to_npc(staker.staked_amount),
        "tier": staker.tier,
        "first_stake_height": staker.first_stake_height,
        "last_activity_height": staker.last_activity_height,
        "pending_unstakes": pending,
    }


@router.get("/pending-rewards/{address}")
async def get_pending_rewards(
    address: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """Return claimable validator rewards for this address.

    Earnings come from L1 fees_to_stakers paid into NPCREWARDSWALLET each block,
    distributed proportionally to active validator stake. This endpoint reads
    only — to actually claim, POST to /api/v1/claim-rewards with a Dilithium3
    signature.
    """
    canonical = canonicalize(address)
    rec = await session.get(ValidatorReward, canonical)

    if rec is None:
        return {
            "address": canonical,
            "input_address": address,
            "pending_rewards": 0,
            "pending_rewards_npc": "0.0000",
            "total_claimed": 0,
            "total_claimed_npc": "0.0000",
            "last_credited_height": None,
            "last_claimed_height": None,
        }

    return {
        "address": canonical,
        "input_address": address,
        "pending_rewards": rec.pending_rewards,
        "pending_rewards_npc": _to_npc(rec.pending_rewards),
        "total_claimed": rec.total_claimed,
        "total_claimed_npc": _to_npc(rec.total_claimed),
        "last_credited_height": rec.last_credited_height,
        "last_claimed_height": rec.last_claimed_height,
    }


@router.get("/rewards-pool/stats")
async def get_rewards_pool_stats(session: AsyncSession = Depends(get_session)) -> dict:
    """Aggregate stats about the validator rewards pool.

    Inflow side: how many blocks have paid in, total amount, total dust.
    Validator side: how much pending across all validators, how much claimed total.
    """
    from sqlalchemy import func

    # Pool inflows
    inflow_sum = await session.execute(
        select(
            func.coalesce(func.sum(RewardPoolInflow.amount), 0),
            func.coalesce(func.sum(RewardPoolInflow.dust_remaining), 0),
            func.count(RewardPoolInflow.id),
        )
    )
    total_in, total_dust, blocks_processed = inflow_sum.one()

    # Validator rewards aggregate
    reward_sum = await session.execute(
        select(
            func.coalesce(func.sum(ValidatorReward.pending_rewards), 0),
            func.coalesce(func.sum(ValidatorReward.total_claimed), 0),
            func.count(ValidatorReward.address),
        )
    )
    total_pending, total_claimed, validator_rows = reward_sum.one()

    return {
        "blocks_processed": blocks_processed,
        "total_inflow": total_in,
        "total_inflow_npc": _to_npc(total_in),
        "total_dust": total_dust,
        "total_dust_npc": _to_npc(total_dust),
        "total_pending": total_pending,
        "total_pending_npc": _to_npc(total_pending),
        "total_claimed_lifetime": total_claimed,
        "total_claimed_lifetime_npc": _to_npc(total_claimed),
        "validators_with_rewards": validator_rows,
    }


@router.get("/leaderboard")
async def leaderboard(
    limit: int = 50, session: AsyncSession = Depends(get_session)
) -> dict:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be 1-500")

    q = (
        select(Staker)
        .where(Staker.staked_amount > 0)
        .order_by(desc(Staker.staked_amount))
        .limit(limit)
    )
    result = await session.execute(q)
    stakers = list(result.scalars())

    return {
        "count": len(stakers),
        "stakers": [
            {
                "rank": i + 1,
                "address": s.address,
                "staked_amount": s.staked_amount,
                "staked_amount_npc": _to_npc(s.staked_amount),
                "tier": s.tier,
            }
            for i, s in enumerate(stakers)
        ],
    }


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    state = await session.get(WatcherState, 1)

    if state is None:
        return {
            "status": "uninitialized",
            "snapshot_complete": False,
            "last_processed_height": 0,
        }

    return {
        "status": "ok" if state.snapshot_complete else "snapshotting",
        "snapshot_complete": state.snapshot_complete,
        "snapshot_started_at": state.snapshot_started_at.isoformat()
        if state.snapshot_started_at
        else None,
        "snapshot_finished_at": state.snapshot_finished_at.isoformat()
        if state.snapshot_finished_at
        else None,
        "last_processed_height": state.last_processed_height,
    }
