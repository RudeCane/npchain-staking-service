"""L1 RPC client for the NPChain testnet node.

Wraps httpx with retry + timeout. Read-only — never POSTs anything.

The L1 node exposes endpoints documented in testnet_node.cpp around line 6459+.
We use:
    GET /api/v1/status                 — chain tip, total supply, peer count
    GET /api/v1/balance/{addr}         — single-address liquid balance
    GET /api/v1/balances               — all balances dump
    GET /api/v1/blocks                 — last 10 blocks (metadata only, no txs!)
    GET /api/v1/tx/history/{addr}      — tx history for an address (in-memory only,
                                          does NOT include historical txns from disk)

Phase A NOTE: the node does NOT expose a "get block by height" endpoint that
returns transaction details. To walk chain history we'll need to either:
  (a) Add such an endpoint to L1 (small C++ change, no consensus impact), or
  (b) Have the staking service co-locate with the node and read npchain_blocks.dat

Phase A is built assuming option (a) will land. We code against a future endpoint
GET /api/v1/block/{height} that returns full block contents including all txs.
"""

from __future__ import annotations

import httpx
import asyncio
from typing import Optional
import structlog

from src.config import settings

log = structlog.get_logger(__name__)


class ChainRPCError(Exception):
    pass


class ChainRPCClient:
    """Thin async wrapper over the L1 HTTP API."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = (base_url or settings.l1_rpc_url).rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ChainRPCClient":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"User-Agent": "npchain-staking/0.1"},
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("ChainRPCClient must be used as async context manager")
        return self._client

    async def _get(self, path: str, max_retries: int = 5) -> dict:
        """GET with exponential backoff on 429 and 5xx errors."""
        delay = 1.0
        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                r = await self.client.get(path)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    log.warning(
                        "rpc_throttled_or_5xx",
                        path=path,
                        status=r.status_code,
                        attempt=attempt + 1,
                        sleep_s=delay,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30.0)
                    continue
                r.raise_for_status()
                await asyncio.sleep(0.05)
                return r.json()
            except httpx.HTTPError as e:
                last_error = e
                log.error("rpc_get_failed", path=path, error=str(e), attempt=attempt + 1)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
        log.error("rpc_get_exhausted", path=path, error=str(last_error))
        raise ChainRPCError(f"GET {path} failed after {max_retries} retries: {last_error}")

    async def submit_tx(self, payload: dict) -> dict:
        """POST a fully-signed transaction to /api/v1/tx/submit.

        payload must contain (all values are strings except memo/data):
            type:           "transfer" | "stake" | "unstake" | "delegate" | "undelegate" | "data"
            from:           sender NPC address
            to:             recipient NPC address
            amount:         base units as decimal string
            fee:            base units as decimal string
            nonce:          decimal string
            timestamp:      unix seconds as decimal string
            memo:           utf-8 string (may be empty)
            data:           utf-8 string (may be empty)
            dilithium_sig:  hex-encoded 3309-byte signature
            sender_pubkey:  hex-encoded 1952-byte public key

        Returns the parsed JSON response. The L1 endpoint always returns 200,
        even for rejections — caller must inspect the body for an "error" key.

        Raises ChainRPCError on transport failure.
        """
        delay = 1.0
        last_error: Optional[Exception] = None
        for attempt in range(5):
            try:
                r = await self.client.post("/api/v1/tx/submit", json=payload)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    log.warning(
                        "submit_tx_throttled_or_5xx",
                        status=r.status_code,
                        attempt=attempt + 1,
                        sleep_s=delay,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30.0)
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.HTTPError as e:
                last_error = e
                log.error("submit_tx_failed", error=str(e), attempt=attempt + 1)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
        raise ChainRPCError(f"submit_tx failed after 5 retries: {last_error}")

    async def status(self) -> dict:
        """Returns chain tip metadata."""
        return await self._get("/api/v1/status")

    async def balance(self, address: str) -> int:
        """Liquid balance for an address, in BASE units (NPC × 1e8)."""
        data = await self._get(f"/api/v1/balance/{address}")
        return int(data.get("balance_base", 0))

    async def block(self, height: int) -> dict:
        """Full block contents at a given height.

        Expected response shape (target schema for the future endpoint):
        {
            "height": 1234,
            "hash": "abc...",
            "miner": "NPC...",
            "reward": 250000000000,
            "timestamp": 1714512000,
            "transactions": [
                {
                    "tx_hash": "...",
                    "type": "transfer" | "stake" | "unstake" | ...,
                    "from": "NPC...",
                    "to": "NPC...",
                    "amount": 100000000,
                    "fee": 1000000,
                    "confirmed_height": 1234
                },
                ...
            ]
        }

        IMPORTANT: This endpoint does not exist yet on L1. Phase A includes
        a stub that raises ChainRPCError("not_implemented") so tests can be
        written; before going live against mainnet we add the endpoint to
        L1's RPC server (lines around 6476 in testnet_node.cpp).
        """
        return await self._get(f"/api/v1/block/{height}")

    async def latest_height(self) -> int:
        s = await self.status()
        return int(s.get("height", 0))
