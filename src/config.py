"""Centralized config — reads from .env, exposes typed settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    db_url: str = "postgresql+asyncpg://npchain:npchain_dev_pw@localhost:5432/npchain_staking"

    l1_rpc_url: str = "http://34.75.143.141:18333"
    pool_address: str = "NPC1006639349b6aceb65fc78be92a404e41edb7019"
    # Alias kept temporarily for ledger.py compatibility — removed in cleanup.
    stake_address: str = "NPC1006639349b6aceb65fc78be92a404e41edb7019"

    watcher_poll_interval: int = 5
    snapshot_batch_size: int = 100

    api_host: str = "127.0.0.1"
    api_port: int = 8950

    log_level: str = "INFO"

    unstake_cooldown_blocks: int = 10080  # 7 days @ 60s blocks

    # Tier thresholds (NPC × 1e8 = base units)
    tier_bronze_min: int = 1_000_000 * 100_000_000
    tier_silver_min: int = 10_000_000 * 100_000_000
    tier_gold_min: int = 100_000_000 * 100_000_000
    tier_diamond_min: int = 500_000_000 * 100_000_000



settings = Settings()
