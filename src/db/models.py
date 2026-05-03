"""SQLAlchemy models for the staking service.

All amounts stored in base units (NPC * 1e8). Avoid floats anywhere they could
introduce rounding error in financial math.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class WatcherState(Base):
    """Singleton row tracking how far the watcher has scanned.

    Only one row, id=1. Updated transactionally with each block processed
    so on restart we resume from last_processed_height + 1.
    """

    __tablename__ = "watcher_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_processed_height: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    snapshot_complete: Mapped[bool] = mapped_column(default=False, nullable=False)
    snapshot_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Staker(Base):
    """One row per address that has ever interacted with escrow.

    `staked_amount` = current locked amount in escrow on this address's behalf.
    `tier` = derived from staked_amount; cached so we don't recompute on every read.
    """

    __tablename__ = "stakers"

    address: Mapped[str] = mapped_column(String(64), primary_key=True)
    staked_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="None")
    first_stake_height: Mapped[int | None] = mapped_column(BigInteger)
    last_activity_height: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    events: Mapped[list["StakeEvent"]] = relationship(back_populates="staker")
    unstake_requests: Mapped[list["UnstakeRequest"]] = relationship(back_populates="staker")


class StakeEvent(Base):
    """Append-only log of every stake-affecting action.

    Source of truth for "what happened, when." If staker.staked_amount ever
    diverges from sum of events, we have a bug to find.
    """

    __tablename__ = "stake_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(
        String(64), ForeignKey("stakers.address"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # STAKE, UNSTAKE, REWARD
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_height: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    tx_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    note: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    staker: Mapped["Staker"] = relationship(back_populates="events")

    __table_args__ = (
        # Same tx_hash should never produce two events for the same address
        UniqueConstraint("address", "tx_hash", "event_type", name="uq_stake_event_tx"),
        Index("ix_stake_events_height_addr", "block_height", "address"),
    )


class UnstakeRequest(Base):
    """Pending unstake with cooldown.

    Lifecycle: PENDING → READY (after unlock_height) → COMPLETED (refund signed/sent)
    or CANCELLED (user changed mind during cooldown).
    """

    __tablename__ = "unstake_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(
        String(64), ForeignKey("stakers.address"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_height: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unlock_height: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING"
    )  # PENDING / READY / COMPLETED / CANCELLED
    refund_tx_hash: Mapped[str | None] = mapped_column(String(128))
    completed_height: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    staker: Mapped["Staker"] = relationship(back_populates="unstake_requests")


class PendingPayout(Base):
    """Outbound on-chain TRANSFER waiting to be signed + submitted.

    Created when:
      - User claims rewards (kind=REWARD_CLAIM)
      - Unstake cooldown expires (kind=UNSTAKE_REFUND)

    Lifecycle:
      QUEUED — newly created, waiting for signer pickup
      SIGNING — signer locked the row and is building/signing the tx
      SUBMITTED — tx submitted to L1, waiting confirmation
      CONFIRMED — tx confirmed in a block
      FAILED — terminal failure; manual ops needed

    `ref` is used for idempotency — if the same logical event re-fires for
    any reason, the unique constraint prevents duplicate payouts.
    """

    __tablename__ = "pending_payouts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recipient: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED", index=True)
    ref: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    tx_hash: Mapped[str | None] = mapped_column(String(128))
    submitted_height: Mapped[int | None] = mapped_column(BigInteger)
    confirmed_height: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str | None] = mapped_column(String(512))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
