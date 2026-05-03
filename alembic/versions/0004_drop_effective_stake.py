"""drop stakers.effective_stake column

No longer used: tier multipliers were rewards-era math. Validators are
ranked and gated by raw staked_amount in the off-L1 model.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-03

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("stakers", "effective_stake")


def downgrade() -> None:
    op.add_column(
        "stakers",
        sa.Column("effective_stake", sa.BigInteger(), nullable=False, server_default="0"),
    )
