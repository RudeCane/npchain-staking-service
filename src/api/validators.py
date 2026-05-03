"""Validator-list endpoint.

In Phase A "validator" = "any staker with non-zero stake."
Phase F adds the foundation whitelist gate; until then the list of validators
is just everyone with stake.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Staker
from src.db.session import get_session

router = APIRouter(prefix="/api/v1", tags=["validators"])


@router.get("/validators")
async def list_validators(session: AsyncSession = Depends(get_session)) -> dict:
    q = (
        select(Staker)
        .where(Staker.staked_amount > 0)
        .order_by(desc(Staker.staked_amount))
    )
    result = await session.execute(q)
    stakers = list(result.scalars())

    return {
        "count": len(stakers),
        "validators": [
            {
                "address": s.address,
                "tier": s.tier,
                "staked_amount": s.staked_amount,
                "staked_amount_npc": f"{s.staked_amount / 100_000_000:.4f}",
            }
            for s in stakers
        ],
    }
