"""Live block watcher.

After the snapshot completes, this loop polls L1 for new blocks every
WATCHER_POLL_INTERVAL seconds and applies each new block's transactions
to the staking ledger.

Survives RPC errors and retries. Persists last_processed_height after
each block so a restart resumes cleanly.
"""

from __future__ import annotations

import asyncio
import structlog

from src.chain.client import ChainRPCClient, ChainRPCError
from src.config import settings
from src.db.models import WatcherState
from src.db.session import session_scope
from src.signing.unstake_processor import process_unlocked_unstakes
from src.staking.ledger import apply_block_transactions

log = structlog.get_logger(__name__)


async def watcher_loop() -> None:
    """Run forever. Polls L1, applies new blocks."""
    log.info("watcher_starting", interval=settings.watcher_poll_interval)

    while True:
        try:
            await _tick()
        except Exception as e:  # noqa: BLE001 — top-level loop must not die
            log.error("watcher_tick_failed", error=str(e), exc_info=True)

        await asyncio.sleep(settings.watcher_poll_interval)


async def _tick() -> None:
    async with ChainRPCClient() as rpc:
        tip = await rpc.latest_height()

        async with session_scope() as session:
            state = await session.get(WatcherState, 1)
            if state is None:
                log.warning("watcher_no_state — run snapshot first")
                return

            if not state.snapshot_complete:
                log.info("watcher_waiting_for_snapshot")
                return

            last = state.last_processed_height

        if tip <= last:
            return  # no new blocks

        log.info("watcher_processing", from_=last + 1, to=tip)

        for h in range(last + 1, tip + 1):
            try:
                block = await rpc.block(h)
            except ChainRPCError as e:
                log.error("watcher_block_fetch_failed", height=h, error=str(e))
                return  # bail, retry on next tick

            async with session_scope() as session:
                await apply_block_transactions(session, block)
                state = await session.get(WatcherState, 1)
                if state is not None:
                    state.last_processed_height = h

                # Process unlocked unstakes — queue refund payouts
                await process_unlocked_unstakes(session, current_height=h)
