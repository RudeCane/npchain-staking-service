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
from src.db.models import StakeEvent, Staker
from src.migrations import canonicalize
from src.staking.tiers import tier_for

log = structlog.get_logger(__name__)


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
