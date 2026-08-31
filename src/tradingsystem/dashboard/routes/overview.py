"""GET /api/overview — portfolio snapshot, heartbeat freshness, active circuit breakers."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tradingsystem.dashboard.dependencies import get_db
from tradingsystem.dashboard.schemas import BreakerOut, HeartbeatOut, OverviewResponse, OverviewSnapshot
from tradingsystem.db.models import CircuitBreakerEvent, PortfolioSnapshot
from tradingsystem.orchestration import heartbeat as heartbeat_module

router = APIRouter()

HEARTBEAT_STALE_AFTER = datetime.timedelta(hours=12)


@router.get("/overview", response_model=OverviewResponse)
def get_overview(db: Session = Depends(get_db)) -> OverviewResponse:
    snapshot = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.snapshot_date.desc()).first()
    heartbeat = heartbeat_module.get_heartbeat(db)
    heartbeat_stale = (
        heartbeat is not None
        and datetime.datetime.utcnow() - heartbeat.last_seen_at > HEARTBEAT_STALE_AFTER
    )
    active_breakers = (
        db.query(CircuitBreakerEvent)
        .filter(CircuitBreakerEvent.cleared_at.is_(None))
        .order_by(CircuitBreakerEvent.tripped_at.desc())
        .all()
    )
    return OverviewResponse(
        snapshot=OverviewSnapshot.model_validate(snapshot) if snapshot else None,
        heartbeat=HeartbeatOut.model_validate(heartbeat) if heartbeat else None,
        heartbeat_stale=heartbeat_stale,
        active_breakers=[BreakerOut.model_validate(b) for b in active_breakers],
    )
