"""Tier calculation. Pure functions, no DB, no IO.

All amounts are BASE units (NPC * 1e8). Tiers gate validator status — no
multiplier math anymore (rewards-via-pool-distribution was scrapped).
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config import settings


@dataclass(frozen=True)
class Tier:
    name: str
    min_stake: int  # base units


TIERS = (
    Tier("Diamond", settings.tier_diamond_min),
    Tier("Gold", settings.tier_gold_min),
    Tier("Silver", settings.tier_silver_min),
    Tier("Bronze", settings.tier_bronze_min),
)
NONE_TIER = Tier("None", 0)


def tier_for(staked_amount_base: int) -> Tier:
    """Returns the matching tier for a stake amount in base units."""
    if staked_amount_base <= 0:
        return NONE_TIER
    for t in TIERS:  # ordered Diamond -> Bronze, return highest matching
        if staked_amount_base >= t.min_stake:
            return t
    return NONE_TIER
