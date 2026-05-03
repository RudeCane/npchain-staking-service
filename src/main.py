"""FastAPI entry point.

Run with:
    uvicorn src.main:app --reload --port 8950

On startup:
  - Configure structured logging
  - Spawn the live watcher background task (only if snapshot is complete;
    otherwise the watcher idles until run_snapshot() is invoked manually)
  - Mount API routers
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI

from src.api import stake as stake_api
from src.api import validators as validators_api
from src.api import write as write_api
from src.chain.watcher import watcher_loop
from src.config import settings
from src.signing.runner import SignerRunner


def _setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    log = structlog.get_logger("main")
    log.info("starting_staking_service", api_port=settings.api_port)

    watcher_task = asyncio.create_task(watcher_loop())

    # Phase C: HotKeySigner uses the pool wallet to sign UNSTAKE_REFUND payouts.
    # Pool key is decrypted from poolwallet.key.enc using NPCHAIN_POOL_PASSPHRASE.
    # Service refuses to start if the env var is unset or the keyfile won't decrypt.
    from src.signing.hot_key_signer import HotKeySigner
    signer_runner = SignerRunner(signer=HotKeySigner())
    signer_task = asyncio.create_task(signer_runner.loop_forever())

    try:
        yield
    finally:
        log.info("shutting_down")
        signer_runner.stop()
        watcher_task.cancel()
        signer_task.cancel()
        for t in (watcher_task, signer_task):
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="NPChain Staking Service",
    version="0.2.0",
    description=(
        "Off-chain staking ledger for NPChain. Phase B includes write API; "
        "Phase C ships with NullSigner — wire a real signer before production."
    ),
    lifespan=lifespan,
)

app.include_router(stake_api.router)
app.include_router(validators_api.router)
app.include_router(write_api.router)


@app.get("/")
async def root() -> dict:
    return {
        "service": "npchain-staking",
        "version": "0.1.0",
        "phase": "A",
        "docs": "/docs",
    }


def main() -> None:
    uvicorn.run(
        "src.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
