"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-01

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watcher_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_processed_height", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("snapshot_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("snapshot_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "stakers",
        sa.Column("address", sa.String(64), primary_key=True),
        sa.Column("staked_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("effective_stake", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tier", sa.String(16), nullable=False, server_default="None"),
        sa.Column("claimable_rewards", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_rewards_earned", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("first_stake_height", sa.BigInteger(), nullable=True),
        sa.Column("last_activity_height", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "stake_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("address", sa.String(64), sa.ForeignKey("stakers.address"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("block_height", sa.BigInteger(), nullable=False),
        sa.Column("tx_hash", sa.String(128), nullable=True),
        sa.Column("note", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("address", "tx_hash", "event_type", name="uq_stake_event_tx"),
    )
    op.create_index("ix_stake_events_address", "stake_events", ["address"])
    op.create_index("ix_stake_events_block_height", "stake_events", ["block_height"])
    op.create_index("ix_stake_events_tx_hash", "stake_events", ["tx_hash"])
    op.create_index("ix_stake_events_height_addr", "stake_events", ["block_height", "address"])

    op.create_table(
        "unstake_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("address", sa.String(64), sa.ForeignKey("stakers.address"), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("request_height", sa.BigInteger(), nullable=False),
        sa.Column("unlock_height", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("refund_tx_hash", sa.String(128), nullable=True),
        sa.Column("completed_height", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_unstake_requests_address", "unstake_requests", ["address"])
    op.create_index("ix_unstake_requests_unlock_height", "unstake_requests", ["unlock_height"])

    op.create_table(
        "reward_distributions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("block_height", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("pool_amount", sa.BigInteger(), nullable=False),
        sa.Column("total_effective_stake", sa.BigInteger(), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("distributed_amount", sa.BigInteger(), nullable=False),
        sa.Column("dust", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("reward_distributions")
    op.drop_index("ix_unstake_requests_unlock_height", table_name="unstake_requests")
    op.drop_index("ix_unstake_requests_address", table_name="unstake_requests")
    op.drop_table("unstake_requests")
    op.drop_index("ix_stake_events_height_addr", table_name="stake_events")
    op.drop_index("ix_stake_events_tx_hash", table_name="stake_events")
    op.drop_index("ix_stake_events_block_height", table_name="stake_events")
    op.drop_index("ix_stake_events_address", table_name="stake_events")
    op.drop_table("stake_events")
    op.drop_table("stakers")
    op.drop_table("watcher_state")
