"""GET /api/pnl, GET /api/pnl/series — snapshot/realized-P&L history and the equity-curve chart series."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tradingsystem.dashboard.dependencies import get_db
from tradingsystem.dashboard.schemas import OverviewSnapshot, PnlResponse, PnlSeriesPoint, PnlSeriesResponse, RealizedPnlOut
from tradingsystem.db.models import PortfolioSnapshot, RealizedPnl

router = APIRouter()


@router.get("/pnl", response_model=PnlResponse)
def get_pnl(db: Session = Depends(get_db)) -> PnlResponse:
    snapshots = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.snapshot_date.desc()).all()
    realized = db.query(RealizedPnl).order_by(RealizedPnl.closed_at.desc()).all()
    return PnlResponse(
        snapshots=[OverviewSnapshot.model_validate(s) for s in snapshots],
        realized=[RealizedPnlOut.model_validate(r) for r in realized],
    )


@router.get("/pnl/series", response_model=PnlSeriesResponse)
def get_pnl_series(db: Session = Depends(get_db)) -> PnlSeriesResponse:
    snapshots = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.snapshot_date.asc()).all()
    return PnlSeriesResponse(
        points=[PnlSeriesPoint(date=s.snapshot_date, equity=s.equity) for s in snapshots],
    )
