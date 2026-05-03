"""add pending_payouts table for foundation-signed transfers

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-01

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_payouts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("recipient", sa.String(64), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        # kind: REWARD_CLAIM | UNSTAKE_REFUND
        sa.Column("kind", sa.String(32), nullable=False),
        # status: QUEUED → SIGNING → SUBMITTED → CONFIRMED | FAILED
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        # idempotency reference (e.g., "reward_claim:<nonce>" or "unstake:<id>")
        sa.Column("ref", sa.String(128), nullable=False, unique=True),
        sa.Column("tx_hash", sa.String(128), nullable=True),
        sa.Column("submitted_height", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_height", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.String(512), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_pending_payouts_status", "pending_payouts", ["status"])
    op.create_index("ix_pending_payouts_recipient", "pending_payouts", ["recipient"])


def downgrade() -> None:
    op.drop_index("ix_pending_payouts_recipient", table_name="pending_payouts")
    op.drop_index("ix_pending_payouts_status", table_name="pending_payouts")
    op.drop_table("pending_payouts")
