"""SQLAlchemy 2.0 declarative models for the second-brain schema (ARCHITECTURE.md §3).

Nine tables: agent_runs, decisions, debate_transcripts, orders, fills,
portfolio_snapshots, realized_pnl, circuit_breaker_events, trading_memory_entries.
"""

from __future__ import annotations

import datetime
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Provisional placeholder — not yet tied to a chosen embedding model. That choice
# belongs to the TradingAgents integration pass; safe to change now since no
# embeddings have been written yet.
EMBEDDING_DIM = 768


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticker: Mapped[str]
    run_type: Mapped[str]  # "pre_market" | "midday"
    started_at: Mapped[datetime.datetime]
    finished_at: Mapped[datetime.datetime | None]
    market_status: Mapped[str]  # "open" | "closed" | "unknown"
    outcome: Mapped[str | None]
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)

    decisions: Mapped[list["Decision"]] = relationship(back_populates="agent_run")
    debate_transcripts: Mapped[list["DebateTranscript"]] = relationship(back_populates="agent_run")


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    agent_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"))
    decision: Mapped[str]  # "buy" | "sell" | "hold"
    reasoning_summary: Mapped[str]
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)

    agent_run: Mapped[AgentRun] = relationship(back_populates="decisions")
    orders: Mapped[list["Order"]] = relationship(back_populates="decision")


class DebateTranscript(Base):
    __tablename__ = "debate_transcripts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    agent_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"))
    role: Mapped[str]  # e.g. "fundamentals_analyst", "bull_researcher", "trader", ...
    content: Mapped[str]
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)

    agent_run: Mapped[AgentRun] = relationship(back_populates="debate_transcripts")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decisions.id"))
    ticker: Mapped[str]
    side: Mapped[str]  # "buy" | "sell"
    qty: Mapped[float]
    limit_price: Mapped[float] = mapped_column(Numeric(12, 4))
    status: Mapped[str]  # "submitted" | "filled" | "partially_filled" | "rejected" | "cancelled"
    alpaca_order_id: Mapped[str | None]
    submitted_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)

    decision: Mapped[Decision] = relationship(back_populates="orders")
    fills: Mapped[list["Fill"]] = relationship(back_populates="order")


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    fill_price: Mapped[float] = mapped_column(Numeric(12, 4))
    fill_qty: Mapped[float]
    filled_at: Mapped[datetime.datetime]

    order: Mapped[Order] = relationship(back_populates="fills")


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[uuid.UUID] = _uuid_pk()
    snapshot_date: Mapped[datetime.date]
    equity: Mapped[float] = mapped_column(Numeric(14, 4))
    cash: Mapped[float] = mapped_column(Numeric(14, 4))
    positions: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)


class RealizedPnl(Base):
    __tablename__ = "realized_pnl"

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticker: Mapped[str]
    decision_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    pnl_amount: Mapped[float] = mapped_column(Numeric(14, 4))
    closed_at: Mapped[datetime.datetime]


class CircuitBreakerEvent(Base):
    __tablename__ = "circuit_breaker_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    breaker_type: Mapped[str]  # "daily" | "weekly"
    trigger_reason: Mapped[str]
    tripped_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
    cleared_at: Mapped[datetime.datetime | None]
    cleared_by: Mapped[str | None]
    review_note: Mapped[str | None]


class TradingMemoryEntry(Base):
    __tablename__ = "trading_memory_entries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticker: Mapped[str | None]
    content: Mapped[str]
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    source: Mapped[str] = mapped_column(default="tradingagents_memory")
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
