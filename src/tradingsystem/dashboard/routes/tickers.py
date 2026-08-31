"""GET /api/ticker/{symbol} — drill-down hub: decision history + orders for one ticker."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tradingsystem.dashboard.dependencies import get_db
from tradingsystem.dashboard.routes.decisions import _to_summary
from tradingsystem.dashboard.routes.orders import _to_order_out
from tradingsystem.dashboard.schemas import TickerDetailResponse
from tradingsystem.db.models import AgentRun, Order

router = APIRouter()


@router.get("/ticker/{symbol}", response_model=TickerDetailResponse)
def get_ticker_detail(symbol: str, db: Session = Depends(get_db)) -> TickerDetailResponse:
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.ticker == symbol)
        .order_by(AgentRun.started_at.desc())
        .all()
    )
    orders = (
        db.query(Order)
        .filter(Order.ticker == symbol)
        .order_by(Order.submitted_at.desc())
        .all()
    )
    return TickerDetailResponse(
        ticker=symbol,
        runs=[_to_summary(r) for r in runs],
        orders=[_to_order_out(o) for o in orders],
    )
