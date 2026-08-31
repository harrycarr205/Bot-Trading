"""GET /api/positions — live positions (via Alpaca) plus the candidate universe."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tradingsystem.config import REPO_ROOT
from tradingsystem.dashboard import config_editing
from tradingsystem.dashboard.dependencies import get_alpaca_client, get_db
from tradingsystem.dashboard.schemas import CandidateOut, PositionOut, PositionsResponse
from tradingsystem.db.models import AgentRun
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol

router = APIRouter()

_CANDIDATE_UNIVERSE_PATH = REPO_ROOT / "config" / "candidate_universe.yaml"


def _latest_decision_for(db: Session, ticker: str) -> tuple[str | None, str | None]:
    run = (
        db.query(AgentRun)
        .filter(AgentRun.ticker == ticker)
        .order_by(AgentRun.started_at.desc())
        .first()
    )
    if run is None or not run.decisions:
        return None, None
    decision = run.decisions[0]
    return decision.decision, decision.rating


@router.get("/positions", response_model=PositionsResponse)
def get_positions(
    db: Session = Depends(get_db),
    alpaca_client: AlpacaClientProtocol = Depends(get_alpaca_client),
) -> PositionsResponse:
    details = alpaca_client.get_position_details()
    positions = []
    for p in details:
        decision, rating = _latest_decision_for(db, p.ticker)
        positions.append(PositionOut(
            ticker=p.ticker,
            qty=p.qty,
            avg_entry_price=p.avg_entry_price,
            current_price=p.current_price,
            unrealized_pnl=(p.current_price - p.avg_entry_price) * p.qty,
            latest_decision=decision,
            latest_rating=rating,
        ))
    held_tickers = {p.ticker for p in details}
    candidate_tickers = config_editing.read_candidate_universe_tickers(_CANDIDATE_UNIVERSE_PATH)
    candidates = [CandidateOut(ticker=t, held=t in held_tickers) for t in candidate_tickers]
    return PositionsResponse(positions=positions, candidates=candidates)
