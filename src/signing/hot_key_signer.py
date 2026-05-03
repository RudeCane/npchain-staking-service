"""HotKeySigner — implements the PayoutSigner Protocol.

Takes a PendingPayout row (recipient, amount, kind), builds a canonical
TRANSFER tx in L1's wire format, signs with the pool key, and submits.
Returns the tx_hash on success.

Lifecycle (called by signer/runner.py SignerRunner):
    runner picks up QUEUED payout, marks it SIGNING
    runner calls signer.sign_and_submit(payout)
    on success: runner marks SUBMITTED, stores tx_hash
    on raise: runner marks back to QUEUED with attempts++ for retry

L1 wire format (matches Transaction::serialize in testnet_node.cpp:1515):
    type(u8) | timestamp(u64 LE) | nonce(u64 LE)
    | len(u64) + from
    | len(u64) + to
    | amount(u64 LE) | fee(u64 LE)
    | len(u64) + memo
    | len(u64) + data

Nonce: queried from L1 fresh per payout via /api/v1/balance/{addr}.
Fee:    hardcoded 1 NPC (100_000_000 base units) — well above any L1 minimum.
"""
from __future__ import annotations

import hashlib
import struct
import time

import structlog

from src.chain.client import ChainRPCClient, ChainRPCError
from src.db.models import PendingPayout
from src.signing import pool_key
from src.signing.pool_key import PoolKey

log = structlog.get_logger(__name__)

# 1 NPC. L1 min fee scales with serialized tx size; this is an order of
# magnitude above what a TRANSFER will ever cost. Polish later.
PAYOUT_FEE_BASE_UNITS = 100_000_000

# TRANSFER tx type per TxType enum in testnet_node.cpp:1428
TX_TYPE_TRANSFER = 0


def _serialize_canonical(tx: dict) -> bytes:
    """Serialize a tx dict into the exact bytes L1 hashes for tx_hash.

    Must match Transaction::serialize() byte-for-byte. Field order:
    type, timestamp, nonce, from, to, amount, fee, memo, data.
    """
    out = bytearray()
    out.append(tx["type"])
    out += struct.pack("<Q", tx["timestamp"])
    out += struct.pack("<Q", tx["nonce"])
    fb = tx["from"].encode("ascii")
    out += struct.pack("<Q", len(fb)) + fb
    tb = tx["to"].encode("ascii")
    out += struct.pack("<Q", len(tb)) + tb
    out += struct.pack("<Q", tx["amount"])
    out += struct.pack("<Q", tx["fee"])
    mb = tx["memo"].encode("utf-8")
    out += struct.pack("<Q", len(mb)) + mb
    db = tx["data"].encode("utf-8")
    out += struct.pack("<Q", len(db)) + db
    return bytes(out)


class HotKeySigner:
    """Signs payouts using a pool key held in memory.

    Conforms to src.signing.queue.PayoutSigner Protocol. Drop-in for
    NullSigner — assign as the signer in src/main.py SignerRunner().

    Single instance per process. Holds a reference to the cached PoolKey
    so we don't decrypt on every payout.
    """

    def __init__(self) -> None:
        self.key: PoolKey = pool_key.load()  # raises if not loadable
        log.info("hot_key_signer_initialized", pool_address=self.key.address)

    async def _next_nonce(self, rpc: ChainRPCClient) -> int:
        """Query L1 for the pool's current account state, return next nonce."""
        data = await rpc._get(f"/api/v1/balance/{self.key.address}")
        # L1 returns {"address":..., "balance":..., "balance_base":..., "nonce":...}
        current = int(data.get("nonce", 0))
        return current + 1

    async def sign_and_submit(self, payout: PendingPayout) -> str:
        """Build, sign, submit. Returns the on-chain tx_hash on success.

        Raises ChainRPCError on transport failure (runner will retry).
        Raises RuntimeError if L1 rejects the tx for a deterministic reason
        (nonce conflict, insufficient balance, fee too low, signature
        invalid). Runner treats these as transient and retries up to
        MAX_ATTEMPTS, but most aren't recoverable without operator action.
        """
        async with ChainRPCClient() as rpc:
            nonce = await self._next_nonce(rpc)

            tx = {
                "type": TX_TYPE_TRANSFER,
                "timestamp": int(time.time()),
                "nonce": nonce,
                "from": self.key.address,
                "to": payout.recipient,
                "amount": payout.amount,
                "fee": PAYOUT_FEE_BASE_UNITS,
                "memo": f"npchain-staking:{payout.kind}:{payout.id}",
                "data": "",
            }
            canon = _serialize_canonical(tx)
            tx_hash_bytes = hashlib.sha3_256(canon).digest()
            sig = self.key.sign(tx_hash_bytes)

            payload = {
                "type": "transfer",
                "from": tx["from"],
                "to": tx["to"],
                "amount": str(tx["amount"]),
                "fee": str(tx["fee"]),
                "nonce": str(tx["nonce"]),
                "timestamp": str(tx["timestamp"]),
                "memo": tx["memo"],
                "data": tx["data"],
                "dilithium_sig": sig.hex(),
                "sender_pubkey": self.key.public_key.hex(),
            }

            log.info(
                "submitting_payout",
                payout_id=payout.id,
                kind=payout.kind,
                recipient=payout.recipient,
                amount=payout.amount,
                nonce=nonce,
                tx_hash=tx_hash_bytes.hex(),
            )

            resp = await rpc.submit_tx(payload)

        if "error" in resp:
            err = resp["error"]
            log.error(
                "payout_rejected_by_l1",
                payout_id=payout.id,
                error=err,
                response=resp,
            )
            raise RuntimeError(f"l1_rejected: {err}")

        tx_hash_hex = tx_hash_bytes.hex()
        log.info(
            "payout_accepted_by_l1",
            payout_id=payout.id,
            tx_hash=tx_hash_hex,
            nonce=nonce,
        )
        return tx_hash_hex
