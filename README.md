# NPChain Staking Service

Off-chain staking ledger for NPChain. Tracks per-address stake amounts, tier
calculations, reward accruals, and unstake cooldowns by reading L1 chain RPC
and watching transfers to/from the staking escrow address.

**This service is read-only against L1 in Phase A.** It does not sign any
transactions. Reward payouts and unstake refunds are tracked in this service's
DB but actual on-chain TRANSFERs require Phase C (foundation-signing wiring).

## Architecture in 30 seconds

```
L1 chain (NPChain testnet)
    │  RPC: get_blocks, get_block, get_status
    ▼
chain/watcher.py  (async loop, polls every 5s)
    │
    ▼
staking/ledger.py (records stake events, applies tier rules)
    │
    ▼
PostgreSQL (single source of truth for stake amounts going forward)
    │
    ▼
api/* (FastAPI endpoints for wallet/L2/foundation console)
```

## Setup

### Requirements
- Python 3.11+
- PostgreSQL 14+ (or use the docker-compose.yml)
- Access to an L1 node RPC endpoint (default: `http://34.75.143.141:18333`)

### First-time setup

```powershell
cd C:\Users\RudeCane\Downloads\npchain-staking
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env to set DB_URL, L1_RPC_URL, etc.
```

### Database

Either run the docker-compose Postgres:

```powershell
docker-compose up -d postgres
```

Or point `.env` at an existing Postgres. Then init schema:

```powershell
alembic upgrade head
```

### Run the service

```powershell
uvicorn src.main:app --reload --port 8950
```

Service exposes:
- `http://localhost:8950/api/v1/stake/{addr}` — stake info per address
- `http://localhost:8950/api/v1/validators` — current validator list
- `http://localhost:8950/api/v1/leaderboard` — top stakers
- `http://localhost:8950/api/v1/health` — service health
- `http://localhost:8950/docs` — OpenAPI docs (auto-generated)

### Initial snapshot

On first run, the service walks every block from genesis to current tip,
sums escrow contributions per address, and locks that as starting state.
This takes ~30 minutes against a 46K-block chain. After the snapshot is
done, the live watcher takes over.

To trigger snapshot manually:

```powershell
python -m src.chain.snapshot
```

## Project layout

```
src/
├── main.py                 — FastAPI entry point + watcher startup
├── config.py               — env vars, tier thresholds, addresses
├── migrations.py           — hardcoded address migration mapping (memory entry #13)
├── db/
│   ├── models.py           — SQLAlchemy models
│   └── session.py          — async DB session factory
├── chain/
│   ├── client.py           — L1 RPC client
│   ├── watcher.py          — live block watcher loop
│   └── snapshot.py         — one-time genesis-to-tip walker
├── staking/
│   ├── tiers.py            — tier calculation
│   ├── rewards.py          — reward distribution math
│   └── ledger.py           — core stake bookkeeping
└── api/
    ├── stake.py            — stake endpoints
    └── validators.py       — validator endpoints

tests/
└── test_*.py               — pytest

deploy/
└── systemd/                — production deploy units (later)
```

## Tier thresholds

| Tier    | Min Stake (NPC) | Reward Multiplier |
|---------|-----------------|-------------------|
| Bronze  | 1,000,000       | 1.0x              |
| Silver  | 10,000,000      | 1.5x              |
| Gold    | 100,000,000     | 2.0x              |
| Diamond | 500,000,000     | 3.0x              |

## Reward distribution

```
For each reward distribution event:
    effective_stake[a] = stake[a] × tier_multiplier(a)
    total_eff = sum(effective_stake[*])
    share[a] = (reward_pool * effective_stake[a]) / total_eff
```

Rewards accumulate in `claimable_rewards` until the user claims, at which
point Phase C signs and submits a TRANSFER from the foundation operations
wallet to the user's address.

## Phase A scope (complete)

- [x] Project skeleton
- [x] Postgres schema + alembic migration
- [x] L1 RPC client
- [x] Snapshot walker (one-time genesis→tip)
- [x] Live block watcher
- [x] Tier calculation
- [x] Reward math
- [x] Read-only API endpoints
- [x] Unit tests for math (33 tests)

## Phase B (complete) — Write API

- [x] Pydantic schemas for signed write requests
- [x] Replay protection (nonce + timestamp window)
- [x] Dilithium signature verification (STUBBED — Phase C wires real verifier)
- [x] POST `/api/v1/stake/request-unstake` (with 7-day cooldown)
- [x] POST `/api/v1/stake/claim-rewards`
- [x] POST `/api/v1/stake/cancel-unstake`

## Phase C (skeleton complete, needs key custody decision)

- [x] `pending_payouts` table + idempotent enqueue
- [x] PayoutSigner protocol (swappable signer interface)
- [x] NullSigner (default — logs warnings instead of signing)
- [x] SignerRunner background loop with retries + backoff
- [x] Unstake processor (auto-queues refund payouts when cooldown unlocks)
- [ ] **Real signer implementation — needs wallet/key custody decision**
- [ ] Real Dilithium3 signature verification (currently stubbed in `auth.py`)
- [ ] Confirmation watcher (SUBMITTED → CONFIRMED transition)

## L1 patches needed (file: l1_patches/)

- [x] `01_block_endpoint.cpp` — adds `GET /api/v1/block/{height}` and
      `GET /api/v1/blocks/range?from=N&to=M`. Required before snapshot
      can run. Zero consensus impact (read-only RPC).
- [ ] Apply the patch, commit, push, deploy to seeds

## Phase D (TODO) — Wallet integration

- Frontend changes to dual-display liquid + staked balance
- Wire wallet to `/api/v1/stake/{addr}` for read state
- Wire stake/unstake/claim flows to call write API with Dilithium-signed messages

## Phase E (artifacts ready) — Production deployment

- [x] systemd unit (`deploy/systemd/npchain-staking.service`)
- [x] nginx config (`deploy/nginx/npchain-staking.conf`)
- [x] Server provisioning script (`deploy/setup.sh`)
- [ ] GCP us-west1 VM provisioned + setup.sh run
- [ ] Cloudflare DNS for `stake.npchain.org`

## Phase F (TODO, last) — Strip L1 staking code

After staking service is proven in production, delete dead C++ staking code:

- Remove `g_staking`, `Staking` class, `validators[]`, `staking.json`
- Remove `last_confirmed_staking_ids` and the gates we patched
- Remove `distribute_fee_rewards`, `record_block_and_distribute`
- Remove cold-start staking replay
- Remove `STAKE/UNSTAKE/DELEGATE/UNDELEGATE` tx types
- Add `validator_whitelist` (small, foundation-signed) — separate decision
- ~800 lines deleted from L1, ~50 lines added
