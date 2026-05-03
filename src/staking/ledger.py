"""Core stake ledger.

apply_block_transactions() is called for every block (snapshot + watcher).
It inspects each tx and applies stake-relevant effects to the DB.

Stake-relevant tx semantics in NPChain Phase A:

  TRANSFER tx where to == STAKE_ADDRESS:
    user is staking `amount` NPC.
    stakers[from].staked_amount += amount
    record StakeEvent(STAKE)

  TRANSFER tx where from == STAKE_ADDRESS:
    treasury/foundation refunding stake.
    stakers[to].staked_amount -= amount  (clamped at 0)
    record StakeEvent(UNSTAKE)

  Legacy STAKE / UNSTAKE / DELEGATE / UNDELEGATE tx types from the
  pre-simplification chain code are also honored for snapshot purposes.
  Going forward (post Phase F) only TRANSFERs to/from escrow exist.

Address canonicalization happens here — every from/to gets routed through
migrations.canonicalize() so stake amounts aggregate to the user's CURRENT
address even if they migrated.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import RewardPoolInflow, StakeEvent, Staker, ValidatorReward
from src.migrations import canonicalize
from src.staking.tiers import tier_for

log = structlog.get_logger(__name__)


# Per-block validator rewards pool. L1 routes fees_to_stakers here every
# block; we distribute proportionally to active validator stake.
# Must match tx_fees::VALIDATOR_REWARDS_ADDRESS in src/testnet_node.cpp.
VALIDATOR_REWARDS_ADDRESS = "NPCREWARDSWALLET000000000000000000000000000"



async def _get_or_create_staker(session: AsyncSession, address: str) -> Staker:
    staker = await session.get(Staker, address)
    if staker is None:
        staker = Staker(address=address, staked_amount=0, tier="None")
        session.add(staker)
        await session.flush()
    return staker


async def _record_event(
    session: AsyncSession,
    address: str,
    event_type: str,
    amount: int,
    block_height: int,
    tx_hash: str | None,
    note: str | None = None,
) -> bool:
    """Insert a StakeEvent. Returns True if inserted, False if duplicate.

    Uniqueness on (address, tx_hash, event_type) makes replay safe.
    """
    if tx_hash is None:
        # Without tx_hash we can't dedupe; skip recording but log it.
        log.warning("stake_event_no_tx_hash", address=address, type=event_type, height=block_height)
        return False

    evt = StakeEvent(
        address=address,
        event_type=event_type,
        amount=amount,
        block_height=block_height,
        tx_hash=tx_hash,
        note=note,
    )
    session.add(evt)
    try:
        await session.flush()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def _refresh_tier_cache(staker: Staker) -> None:
    """Update tier cache field based on staked_amount."""
    t = tier_for(staker.staked_amount)
    staker.tier = t.name


async def apply_block_transactions(session: AsyncSession, block: dict) -> None:
    """Apply every stake-relevant tx in this block to the DB.

    Idempotent: re-running on the same block produces zero changes thanks to
    the unique constraint on stake_events(address, tx_hash, event_type).
    """
    height = int(block.get("height", 0))
    txs = block.get("transactions", []) or []

    if not txs:
        return

    for tx in txs:
        await _apply_single_tx(session, tx, height)


async def _apply_single_tx(session: AsyncSession, tx: dict, height: int) -> None:
    tx_type = (tx.get("type") or "").lower()
    tx_hash = tx.get("tx_hash") or tx.get("hash")

    raw_from = tx.get("from", "")
    raw_to = tx.get("to", "")
    amount = int(tx.get("amount", 0))
    if amount <= 0:
        return

    addr_from = canonicalize(raw_from)
    addr_to = canonicalize(raw_to)

    # Validator rewards pool inflow: L1 paid into NPCREWARDSWALLET on this block.
    # Distribute the amount proportionally across active validators by stake.
    # Note: NOT canonicalized for the rewards address — it's a hardcoded string,
    # and we want the literal compare to the constant defined above.
    if tx_type == "transfer" and raw_to == VALIDATOR_REWARDS_ADDRESS:
        await _credit_validator_rewards_pool(session, amount, height)
        return

    is_to_escrow = addr_to == settings.stake_address
    is_from_escrow = addr_from == settings.stake_address

    # Case 1: TRANSFER to escrow → STAKE event
    if tx_type in ("transfer", "stake") and is_to_escrow and not is_from_escrow:
        staker = await _get_or_create_staker(session, addr_from)
        inserted = await _record_event(
            session, addr_from, "STAKE", amount, height, tx_hash, note="transfer_to_escrow"
        )
        if inserted:
            staker.staked_amount += amount
            staker.last_activity_height = height
            if staker.first_stake_height is None:
                staker.first_stake_height = height
            await _refresh_tier_cache(staker)
        return

    # Case 2: TRANSFER from escrow → UNSTAKE event (refund)
    if tx_type in ("transfer", "unstake") and is_from_escrow and not is_to_escrow:
        staker = await _get_or_create_staker(session, addr_to)
        inserted = await _record_event(
            session, addr_to, "UNSTAKE", amount, height, tx_hash, note="refund_from_escrow"
        )
        if inserted:
            new_amount = max(0, staker.staked_amount - amount)
            staker.staked_amount = new_amount
            staker.last_activity_height = height
            await _refresh_tier_cache(staker)
        return

    # Case 3: legacy STAKE tx type (pre-Phase F chain code)
    # On the pre-simplification chain, "stake" tx debited from sender + credited
    # escrow without a literal "to" field pointing at escrow. Honor it for
    # snapshot accuracy.
    if tx_type == "stake" and not is_to_escrow:
        staker = await _get_or_create_staker(session, addr_from)
        inserted = await _record_event(
            session, addr_from, "STAKE", amount, height, tx_hash, note="legacy_stake_tx"
        )
        if inserted:
            staker.staked_amount += amount
            staker.last_activity_height = height
            if staker.first_stake_height is None:
                staker.first_stake_height = height
            await _refresh_tier_cache(staker)
        return

    # Other tx types are ignored — not stake-relevant.


async def get_active_stakers(session: AsyncSession) -> list[Staker]:
    """All stakers with non-zero stake. Used by reward distribution."""
    q = select(Staker).where(Staker.staked_amount > 0)
    result = await session.execute(q)
    return list(result.scalars())


async def _credit_validator_rewards_pool(
    session: AsyncSession, amount: int, block_height: int
) -> None:
    """Distribute a single L1 pool inflow across active validators by stake weight.

    Idempotent on block_height — the reward_pool_inflows.block_height UNIQUE
    constraint ensures we credit each block exactly once even if the watcher
    re-processes a block.

    Distribution math is integer-only (no floats). Any rounding dust stays
    in the conceptual pool; we record it on the inflow row so a future
    distribution can sweep it if we want, or it can stay accounted for in
    the audit trail.
    """
    if amount <= 0:
        return

    # Idempotency check: have we already processed this block's inflow?
    existing = await session.execute(
        select(RewardPoolInflow).where(RewardPoolInflow.block_height == block_height)
    )
    if existing.scalar_one_or_none() is not None:
        log.debug(
            "reward_pool_inflow_already_processed",
            height=block_height,
            amount=amount,
        )
        return

    active = await get_active_stakers(session)
    total_stake = sum(s.staked_amount for s in active)

    if total_stake == 0:
        # No validators → record the inflow with zero distribution. Money sits
        # at NPCREWARDSWALLET on L1; we'll let it accumulate for the next
        # distribution that has at least one validator. (Long-term: sweep it
        # back to foundation, but that's a later patch.)
        session.add(
            RewardPoolInflow(
                block_height=block_height,
                amount=amount,
                distributed_to_count=0,
                dust_remaining=amount,
            )
        )
        log.info(
            "reward_pool_inflow_no_validators",
            height=block_height,
            amount=amount,
        )
        return

    distributed = 0
    paid_count = 0
    for staker in active:
        # Integer math: (amount * stake) / total — no floats, no rounding drift.
        share = (amount * staker.staked_amount) // total_stake
        if share <= 0:
            continue

        # Upsert pending_rewards row for this validator.
        existing_reward = await session.get(ValidatorReward, staker.address)
        if existing_reward is None:
            session.add(
                ValidatorReward(
                    address=staker.address,
                    pending_rewards=share,
                    last_credited_height=block_height,
                )
            )
        else:
            existing_reward.pending_rewards += share
            existing_reward.last_credited_height = block_height

        distributed += share
        paid_count += 1

    dust = amount - distributed

    session.add(
        RewardPoolInflow(
            block_height=block_height,
            amount=amount,
            distributed_to_count=paid_count,
            dust_remaining=dust,
        )
    )

    log.info(
        "reward_pool_inflow_distributed",
        height=block_height,
        amount=amount,
        validators=paid_count,
        dust=dust,
    )

