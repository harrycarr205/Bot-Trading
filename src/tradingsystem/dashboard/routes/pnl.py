"""GET /api/pnl, GET /api/pnl/series — snapshot/realized-P&L history and the equity-curve chart series."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tradingsystem.dashboard.dependencies import get_db
from tradingsystem.dashboard.schemas import OverviewSnapshot, PnlResponse, PnlSeriesPoint, PnlSeriesResponse, RealizedPnlOut
from tradingsystem.db.models import Decision, PortfolioSnapshot, RealizedPnl

router = APIRouter()


@router.get("/pnl", response_model=PnlResponse)
def get_pnl(db: Session = Depends(get_db)) -> PnlResponse:
    snapshots = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.snapshot_date.desc()).all()
    realized = db.query(RealizedPnl).order_by(RealizedPnl.closed_at.desc()).all()

    # RealizedPnl stores decision ids, but the decision-detail view is keyed by
    # agent-run id — resolve them here (one batched query for the whole page) so
    # a realized row can link back to the reasoning behind it.
    wanted = {decision_id for row in realized for decision_id in row.decision_ids}
    run_id_by_decision_id = dict(
        db.query(Decision.id, Decision.agent_run_id).filter(Decision.id.in_(wanted)).all()
    ) if wanted else {}

    return PnlResponse(
        snapshots=[OverviewSnapshot.model_validate(s) for s in snapshots],
        realized=[
            RealizedPnlOut(
                id=row.id, ticker=row.ticker, pnl_amount=row.pnl_amount, closed_at=row.closed_at,
                decision_ids=row.decision_ids,
                agent_run_ids=[
                    run_id_by_decision_id[d] for d in row.decision_ids if d in run_id_by_decision_id
                ],
            )
            for row in realized
        ],
    )


@router.get("/pnl/series", response_model=PnlSeriesResponse)
def get_pnl_series(db: Session = Depends(get_db)) -> PnlSeriesResponse:
    snapshots = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.snapshot_date.asc()).all()
    return PnlSeriesResponse(
        points=[PnlSeriesPoint(date=s.snapshot_date, equity=s.equity) for s in snapshots],
    )
