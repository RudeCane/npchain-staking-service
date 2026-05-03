"""drop dead reward fields and reward_distributions table

The off-L1 staking pool model never had rewards-via-pool-distribution.
Stake gates validator status; validators earn from work (mining, L2 tx
hashing, bug bounties), not from staking. The columns and table being
dropped here were scaffolded against an architecture that never existed.

Drops:
  - stakers.claimable_rewards (BigInteger)
  - stakers.total_rewards_earned (BigInteger)
  - reward_distributions table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-03

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("stakers", "claimable_rewards")
    op.drop_column("stakers", "total_rewards_earned")
    op.drop_table("reward_distributions")


def downgrade() -> None:
    op.add_column(
        "stakers",
        sa.Column("claimable_rewards", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "stakers",
        sa.Column("total_rewards_earned", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_table(
        "reward_distributions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("block_height", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("pool_amount", sa.BigInteger(), nullable=False),
        sa.Column("total_effective_stake", sa.BigInteger(), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("distributed_amount", sa.BigInteger(), nullable=False),
        sa.Column("dust", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
