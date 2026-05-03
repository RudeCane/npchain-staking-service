"""validator_rewards table for per-validator earned-from-work pool

This is NOT a reanimation of the dropped reward-via-stake-distribution model
(see 0003_drop_rewards). That model paid stakers for staking. This model pays
VALIDATORS for WORK — block fees that L1 routes to NPCREWARDSWALLET... get
distributed proportionally to active validator stake when validators claim.

Stake gates validator status. Validators earn from work. Pool tracks accrual.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-03
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Per-validator pending rewards (claimable balance).
    # `address` references stakers.address but we don't add an FK so a
    # reward record can briefly outlive a staker if they fully unstake
    # before claiming.
    op.create_table(
        "validator_rewards",
        sa.Column("address", sa.String(64), primary_key=True),
        sa.Column("pending_rewards", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_claimed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_credited_height", sa.BigInteger(), nullable=True),
        sa.Column("last_claimed_height", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_validator_rewards_pending",
        "validator_rewards",
        ["pending_rewards"],
        postgresql_where=sa.text("pending_rewards > 0"),
    )

    # Per-block ledger of pool inflows (audit trail). Lets us verify
    # later that distribution math matches what L1 paid in.
    op.create_table(
        "reward_pool_inflows",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("block_height", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("distributed_to_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dust_remaining", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_reward_pool_inflows_height",
        "reward_pool_inflows",
        ["block_height"],
    )


def downgrade() -> None:
    op.drop_index("ix_reward_pool_inflows_height", table_name="reward_pool_inflows")
    op.drop_table("reward_pool_inflows")
    op.drop_index("ix_validator_rewards_pending", table_name="validator_rewards")
    op.drop_table("validator_rewards")
