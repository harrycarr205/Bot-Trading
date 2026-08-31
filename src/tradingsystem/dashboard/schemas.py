"""Pydantic response models for the dashboard JSON API (/api/*).

One module for every response shape so every route task has a stable,
already-reviewed contract — see docs/superpowers/specs/2026-08-31-dashboard-redesign-design.md §5.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict


class OverviewSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    snapshot_date: datetime.date
    equity: float
    cash: float


class HeartbeatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    last_seen_at: datetime.datetime
    last_run_type: str | None
    last_ticker: str | None


class BreakerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    breaker_type: str
    trigger_reason: str
    tripped_at: datetime.datetime


class OverviewResponse(BaseModel):
    snapshot: OverviewSnapshot | None
    heartbeat: HeartbeatOut | None
    heartbeat_stale: bool
    active_breakers: list[BreakerOut]


class PositionOut(BaseModel):
    ticker: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float
    latest_decision: str | None
    latest_rating: str | None


class CandidateOut(BaseModel):
    ticker: str
    held: bool


class PositionsResponse(BaseModel):
    positions: list[PositionOut]
    candidates: list[CandidateOut]


class DecisionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    rating: str
    decision: str
    reasoning_summary: str


class AgentRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ticker: str
    run_type: str
    started_at: datetime.datetime
    outcome: str | None
    decision: DecisionSummary | None


class DecisionsResponse(BaseModel):
    runs: list[AgentRunSummary]


class TranscriptEntry(BaseModel):
    role: str
    content: str


class DecisionDetailResponse(BaseModel):
    id: uuid.UUID
    ticker: str
    run_type: str
    started_at: datetime.datetime
    outcome: str | None
    market_status: str
    decision: DecisionSummary | None
    transcripts: list[TranscriptEntry]
    order_id: uuid.UUID | None


class FillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    filled_at: datetime.datetime
    fill_qty: float
    fill_price: float


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    submitted_at: datetime.datetime
    ticker: str
    side: str
    qty: float
    limit_price: float
    status: str
    decision_id: uuid.UUID
    agent_run_id: uuid.UUID
    fills: list[FillOut]
    cancellable: bool


class OrdersResponse(BaseModel):
    orders: list[OrderOut]


class TickerDetailResponse(BaseModel):
    ticker: str
    runs: list[AgentRunSummary]
    orders: list[OrderOut]


class RealizedPnlOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ticker: str
    pnl_amount: float
    closed_at: datetime.datetime
    decision_ids: list[uuid.UUID]


class PnlResponse(BaseModel):
    snapshots: list[OverviewSnapshot]
    realized: list[RealizedPnlOut]


class PnlSeriesPoint(BaseModel):
    date: datetime.date
    equity: float


class PnlSeriesResponse(BaseModel):
    points: list[PnlSeriesPoint]


class ProcessStatusOut(BaseModel):
    alive: bool
    pid: int | None


class ControlStatusResponse(BaseModel):
    scheduler: ProcessStatusOut
    watchdog: ProcessStatusOut
    scheduler_log: str | None
    watchdog_log: str | None
    run_once_log: str | None


class RiskConfigOut(BaseModel):
    max_position_pct: float
    cash_reserve_pct: float
    stop_loss_pct: float
    daily_drawdown_breaker_pct: float
    weekly_drawdown_breaker_pct: float
    stale_data_max_age_minutes: int


class ConfigResponse(BaseModel):
    tickers: list[str]
    risk_config: RiskConfigOut
    env_values: dict[str, str | None]
