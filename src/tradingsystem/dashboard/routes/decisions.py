"""GET /api/decisions, GET /api/decisions/{agent_run_id}."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from tradingsystem.dashboard.dependencies import get_db
from tradingsystem.dashboard.schemas import (
    AgentRunSummary, DecisionDetailResponse, DecisionsResponse, DecisionSummary, TranscriptEntry,
)
from tradingsystem.db.models import AgentRun

router = APIRouter()

_ROLE_ORDER = [
    "market_analyst", "sentiment_analyst", "news_analyst", "fundamentals_analyst",
    "bull_researcher", "bear_researcher", "research_manager_judge", "trader",
    "risk_aggressive", "risk_conservative", "risk_neutral", "risk_judge",
    "investment_plan", "portfolio_manager_final_decision",
]
_ROLE_ORDER_INDEX = {role: i for i, role in enumerate(_ROLE_ORDER)}


def _to_summary(run: AgentRun) -> AgentRunSummary:
    decision = run.decisions[0] if run.decisions else None
    return AgentRunSummary(
        id=run.id, ticker=run.ticker, run_type=run.run_type, started_at=run.started_at,
        outcome=run.outcome,
        decision=DecisionSummary.model_validate(decision) if decision else None,
    )


@router.get("/decisions", response_model=DecisionsResponse)
def list_decisions(ticker: str | None = None, db: Session = Depends(get_db)) -> DecisionsResponse:
    query = db.query(AgentRun).order_by(AgentRun.started_at.desc())
    if ticker:
        query = query.filter(AgentRun.ticker == ticker)
    return DecisionsResponse(runs=[_to_summary(run) for run in query.all()])


@router.get("/decisions/{agent_run_id}", response_model=DecisionDetailResponse)
def get_decision_detail(agent_run_id: uuid.UUID, db: Session = Depends(get_db)) -> DecisionDetailResponse:
    run = db.get(AgentRun, agent_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    decision = run.decisions[0] if run.decisions else None
    transcripts = sorted(
        run.debate_transcripts, key=lambda t: _ROLE_ORDER_INDEX.get(t.role, len(_ROLE_ORDER)),
    )
    return DecisionDetailResponse(
        id=run.id, ticker=run.ticker, run_type=run.run_type, started_at=run.started_at,
        outcome=run.outcome, market_status=run.market_status,
        decision=DecisionSummary.model_validate(decision) if decision else None,
        transcripts=[TranscriptEntry(role=t.role, content=t.content) for t in transcripts],
        order_id=decision.orders[0].id if decision and decision.orders else None,
    )
