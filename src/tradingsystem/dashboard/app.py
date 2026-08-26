"""FastAPI dashboard app — read-only web UI over the second-brain database.

ARCHITECTURE.md §7. No write actions, localhost-only, on-demand (python -m
tradingsystem.dashboard). Each route opens its own short-lived session via
get_db and closes it before returning — no session shared across requests.
"""

from __future__ import annotations

import datetime
import pathlib
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, DebateTranscript, Decision, Order, PortfolioSnapshot, RealizedPnl
from tradingsystem.db.session import make_session_factory
from tradingsystem.orchestration import heartbeat as heartbeat_module
from tradingsystem.orchestration import process_control

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


@app.get("/decisions")
def decisions_list(request: Request, ticker: str | None = None, db: Session = Depends(get_db)):
    query = db.query(AgentRun).order_by(AgentRun.started_at.desc())
    if ticker:
        query = query.filter(AgentRun.ticker == ticker)
    runs = query.all()
    rows = [(run, run.decisions[0] if run.decisions else None) for run in runs]
    return templates.TemplateResponse(request, "decisions.html", {"runs": rows, "ticker": ticker})


_ROLE_ORDER = [
    "market_analyst", "sentiment_analyst", "news_analyst", "fundamentals_analyst",
    "bull_researcher", "bear_researcher", "research_manager_judge", "trader",
    "risk_aggressive", "risk_conservative", "risk_neutral", "risk_judge",
    "investment_plan", "portfolio_manager_final_decision",
]
_ROLE_ORDER_INDEX = {role: i for i, role in enumerate(_ROLE_ORDER)}


@app.get("/decisions/{agent_run_id}")
def decision_detail(request: Request, agent_run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = db.get(AgentRun, agent_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    decision = run.decisions[0] if run.decisions else None
    transcripts = sorted(
        run.debate_transcripts, key=lambda t: _ROLE_ORDER_INDEX.get(t.role, len(_ROLE_ORDER))
    )
    rows = [(t.role, t.content) for t in transcripts]
    return templates.TemplateResponse(
        request, "decision_detail.html", {"run": run, "decision": decision, "transcripts": rows}
    )


@app.get("/orders")
def orders_list(request: Request, db: Session = Depends(get_db)):
    orders = db.query(Order).order_by(Order.submitted_at.desc()).all()
    return templates.TemplateResponse(request, "orders.html", {"orders": orders})


@app.get("/pnl")
def pnl(request: Request, db: Session = Depends(get_db)):
    snapshots = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.snapshot_date.desc()).all()
    realized = db.query(RealizedPnl).order_by(RealizedPnl.closed_at.desc()).all()
    return templates.TemplateResponse(request, "pnl.html", {"snapshots": snapshots, "realized": realized})


_STOP_POLL_INTERVAL_SECONDS = 2
_STOP_TIMEOUT_SECONDS = 60
_START_POLL_INTERVAL_SECONDS = 0.5
_START_POLL_ATTEMPTS = 6  # ~3 seconds total


def _tail_log(name: str, lines: int = 200) -> str | None:
    log_path = process_control.RUN_DIR / f"{name}.log"
    if not log_path.exists():
        return None
    return "\n".join(log_path.read_text().splitlines()[-lines:])


@app.get("/control")
def control(request: Request):
    return templates.TemplateResponse(
        request,
        "control.html",
        {
            "scheduler_status": process_control.get_process_status("scheduler"),
            "watchdog_status": process_control.get_process_status("watchdog"),
            "scheduler_log": _tail_log("scheduler"),
            "watchdog_log": _tail_log("watchdog"),
            "scheduler_stop_forced": False,
            "watchdog_stop_forced": False,
        },
    )


@app.post("/control/{name}/start")
def start_process(name: str):
    if name not in ("scheduler", "watchdog"):
        raise HTTPException(status_code=404, detail="Unknown process")
    if process_control.get_process_status(name).alive:
        raise HTTPException(status_code=409, detail=f"{name} is already running")

    process_control.spawn_detached(name)
    for _ in range(_START_POLL_ATTEMPTS):
        time.sleep(_START_POLL_INTERVAL_SECONDS)
        status = process_control.get_process_status(name)
        if status.alive:
            return {"started": True, "pid": status.pid}

    return {"started": True, "pid": None, "note": "spawned but not yet confirmed alive — refresh shortly"}


@app.post("/control/{name}/stop")
def stop_process(name: str):
    if name not in ("scheduler", "watchdog"):
        raise HTTPException(status_code=404, detail="Unknown process")
    status = process_control.get_process_status(name)
    if not status.alive:
        return {"stopped": True, "forced": False, "note": "was not running"}

    process_control.request_stop(name)
    waited = 0.0
    while waited < _STOP_TIMEOUT_SECONDS:
        time.sleep(_STOP_POLL_INTERVAL_SECONDS)
        waited += _STOP_POLL_INTERVAL_SECONDS
        if not process_control.get_process_status(name).alive:
            return {"stopped": True, "forced": False}

    process_control.force_kill(status.pid)
    process_control.remove_pidfile(name)
    return {"stopped": True, "forced": True}


@app.post("/control/run-now")
def run_now():
    pid = process_control.spawn_detached("run_once")
    return {"started": True, "pid": pid}
