"""FastAPI dashboard app — read-only web UI over the second-brain database.

ARCHITECTURE.md §7. No write actions, localhost-only, on-demand (python -m
tradingsystem.dashboard). Each route opens its own short-lived session via
get_db and closes it before returning — no session shared across requests.
"""

from __future__ import annotations

import datetime
import pathlib

from fastapi import Depends, FastAPI
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from tradingsystem.db.models import CircuitBreakerEvent, PortfolioSnapshot, SchedulerHeartbeat
from tradingsystem.db.session import make_session_factory

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _money(value) -> str:
    if value is None:
        return "-"
    return f"${float(value):,.2f}"


templates.env.filters["money"] = _money

app = FastAPI(title="Bot-Trading Dashboard")

_session_factory = make_session_factory()


def get_db():
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


HEARTBEAT_STALE_AFTER = datetime.timedelta(hours=12)


@app.get("/")
def overview(request: Request, db: Session = Depends(get_db)):
    snapshot = (
        db.query(PortfolioSnapshot)
        .order_by(PortfolioSnapshot.snapshot_date.desc())
        .first()
    )
    heartbeat = db.get(SchedulerHeartbeat, "scheduler")
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
    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "snapshot": snapshot,
            "heartbeat": heartbeat,
            "heartbeat_stale": heartbeat_stale,
            "active_breakers": active_breakers,
        },
    )
