"""One-time chain history walker.

Runs once at first deploy. Walks every block from genesis to current tip,
processes every transaction, builds the initial stake_amount per address,
locks the result as DB starting state.

Idempotent: if interrupted, can resume from WatcherState.last_processed_height
and continue without reprocessing already-counted blocks (each StakeEvent has
a unique constraint on (address, tx_hash, event_type) so re-processing the
same block produces no DB changes).
"""

from __future__ import annotations

import asyncio
import structlog

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.chain.client import ChainRPCClient, ChainRPCError
from src.config import settings
from src.db.models import WatcherState
from src.db.session import session_scope
from src.staking.ledger import apply_block_transactions

log = structlog.get_logger(__name__)


async def _get_or_create_watcher_state(session: AsyncSession) -> WatcherState:
    state = await session.get(WatcherState, 1)
    if state is None:
        state = WatcherState(id=1, last_processed_height=0, snapshot_complete=False)
        session.add(state)
        await session.flush()
    return state


async def run_snapshot() -> None:
    """Walk the chain from last_processed_height to current tip.

    On first run: starts from block 0.
    On resumed run: continues from where we stopped.

    Marks snapshot_complete=True when done.
    """
    log.info("snapshot_starting")

    async with ChainRPCClient() as rpc:
        tip = await rpc.latest_height()
        log.info("snapshot_target", tip_height=tip)

        async with session_scope() as session:
            state = await _get_or_create_watcher_state(session)
            start = state.last_processed_height + 1 if state.last_processed_height > 0 else 0
            if state.snapshot_started_at is None:
                from datetime import datetime, timezone
                state.snapshot_started_at = datetime.now(timezone.utc)

            log.info("snapshot_resume_point", start_height=start, tip_height=tip)

        # Process blocks in batches to keep one transaction reasonable.
        height = start
        while height <= tip:
            batch_end = min(height + settings.snapshot_batch_size - 1, tip)
            await _process_batch(rpc, height, batch_end)
            height = batch_end + 1

            # Refresh tip occasionally — chain keeps producing while we walk.
            if height > tip:
                new_tip = await rpc.latest_height()
                if new_tip > tip:
                    log.info("snapshot_tip_advanced", old_tip=tip, new_tip=new_tip)
                    tip = new_tip

        async with session_scope() as session:
            state = await _get_or_create_watcher_state(session)
            state.snapshot_complete = True
            from datetime import datetime, timezone
            state.snapshot_finished_at = datetime.now(timezone.utc)

    log.info("snapshot_complete", final_height=tip)


async def _process_batch(rpc: ChainRPCClient, start_h: int, end_h: int) -> None:
    """Fetch + apply a contiguous range of blocks."""
    log.info("snapshot_batch", start=start_h, end=end_h)

    for h in range(start_h, end_h + 1):
        try:
            block = await rpc.block(h)
        except ChainRPCError as e:
            log.error("snapshot_block_fetch_failed", height=h, error=str(e))
            # Re-raise so we don't silently skip — better to halt and resume than to
            # produce incorrect snapshot state.
            raise

        async with session_scope() as session:
            await apply_block_transactions(session, block)
            state = await _get_or_create_watcher_state(session)
            state.last_processed_height = h


async def main() -> None:
    """CLI entry point: `python -m src.chain.snapshot`"""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    await run_snapshot()


if __name__ == "__main__":
    asyncio.run(main())
