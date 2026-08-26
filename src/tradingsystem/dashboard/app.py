"""FastAPI dashboard app over the second-brain database — mostly read-only,
plus a small process-control surface (ARCHITECTURE.md §7): start/stop the
scheduler/watchdog, trigger an off-cycle run, and view their logs. Every
write route is guarded by _require_same_origin (a same-origin/CSRF check)
and, for start, an in-process lock serializing the check-then-spawn
sequence. Circuit-breaker clearing and the kill switch remain CLI-only —
deliberately excluded, see ARCHITECTURE.md §7. localhost-only, no
authentication (single-operator tool). Each route opens its own
short-lived session via get_db and closes it before returning — no
session shared across requests.
"""

from __future__ import annotations

import datetime
import pathlib
import threading
import time
import urllib.parse
import uuid

from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from tradingsystem.config import REPO_ROOT
from tradingsystem.dashboard import config_editing
from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, DebateTranscript, Decision, Order, PortfolioSnapshot, RealizedPnl
from tradingsystem.db.session import make_session_factory
from tradingsystem.orchestration import heartbeat as heartbeat_module
from tradingsystem.orchestration import process_control

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_RISK_CONFIG_PATH = REPO_ROOT / "config" / "risk_config.yaml"
_CANDIDATE_UNIVERSE_PATH = REPO_ROOT / "config" / "candidate_universe.yaml"
_ENV_PATH = REPO_ROOT / ".env"
_ENV_FIELDS = (
    "discovery_slots_per_cycle", "watchdog_check_interval_minutes",
    "pre_market_cron", "midday_cron",
    "tradingagents_deep_think_model", "tradingagents_quick_think_model",
)


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


@app.get("/config")
def config_page(request: Request):
    env_values = {
        field: config_editing.read_env_value(_ENV_PATH, field.upper())
        for field in _ENV_FIELDS
    }
    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "tickers": config_editing.read_candidate_universe_tickers(_CANDIDATE_UNIVERSE_PATH),
            "risk_config": config_editing.read_risk_config_values(_RISK_CONFIG_PATH),
            "env_values": env_values,
        },
    )


_STOP_POLL_INTERVAL_SECONDS = 2
_STOP_TIMEOUT_SECONDS = 60
_START_POLL_INTERVAL_SECONDS = 0.5
_START_POLL_ATTEMPTS = 6  # ~3 seconds total

# Serializes the check-then-spawn sequence in start_process so two
# near-simultaneous requests (double-click, two open tabs) can't both pass
# the alive-check before either has spawned — this dashboard runs as a
# single local process, so a plain in-process lock is sufficient.
_start_lock = threading.Lock()


def _tail_log(name: str, lines: int = 200) -> str | None:
    log_path = process_control.RUN_DIR / f"{name}.log"
    if not log_path.exists():
        return None
    return "\n".join(log_path.read_text().splitlines()[-lines:])


# This dashboard is documented and designed as localhost-only (Settings.dashboard_host
# defaults to "127.0.0.1") — pinned here rather than derived from the request's own Host
# header, which uvicorn does not validate and a DNS-rebinding attacker could spoof.
_ALLOWED_ORIGIN_HOSTS = {"127.0.0.1", "localhost"}


def _require_same_origin(request: Request) -> None:
    """Rejects cross-origin POSTs to the write routes (CSRF guard).

    Same-origin requests either omit the Origin header (some browsers omit
    it for same-site form navigations) or send one whose host is in
    _ALLOWED_ORIGIN_HOSTS; a cross-origin drive-by form POST sends some
    other Origin, which we reject with 403.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return
    origin_host = urllib.parse.urlsplit(origin).hostname
    if origin_host not in _ALLOWED_ORIGIN_HOSTS:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected")


@app.post("/config/candidate-universe")
def post_candidate_universe(tickers: str = Form(...), _: None = Depends(_require_same_origin)):
    ticker_list = [line.strip() for line in tickers.splitlines() if line.strip()]
    try:
        config_editing.write_candidate_universe(_CANDIDATE_UNIVERSE_PATH, ticker_list)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse("/config", status_code=303)


@app.post("/config/risk-config")
def post_risk_config(
    max_position_pct: float = Form(...),
    cash_reserve_pct: float = Form(...),
    stop_loss_pct: float = Form(...),
    daily_drawdown_breaker_pct: float = Form(...),
    weekly_drawdown_breaker_pct: float = Form(...),
    stale_data_max_age_minutes: int = Form(...),
    note: str = Form(...),
    _: None = Depends(_require_same_origin),
):
    note = note.strip()
    if not note:
        raise HTTPException(status_code=422, detail="justification is required")
    if len(note) > 2000:
        raise HTTPException(status_code=422, detail="justification must be 2000 characters or fewer")

    updates = {
        "max_position_pct": max_position_pct,
        "cash_reserve_pct": cash_reserve_pct,
        "stop_loss_pct": stop_loss_pct,
        "daily_drawdown_breaker_pct": daily_drawdown_breaker_pct,
        "weekly_drawdown_breaker_pct": weekly_drawdown_breaker_pct,
        "stale_data_max_age_minutes": stale_data_max_age_minutes,
    }
    try:
        changes = config_editing.write_risk_config(_RISK_CONFIG_PATH, updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if changes:
        config_editing.append_config_change_log(process_control.RUN_DIR, changes, note)
    return RedirectResponse("/config", status_code=303)


@app.post("/config/env-settings")
def post_env_settings(
    discovery_slots_per_cycle: int = Form(...),
    watchdog_check_interval_minutes: int = Form(...),
    pre_market_cron: str = Form(...),
    midday_cron: str = Form(...),
    tradingagents_deep_think_model: str = Form(...),
    tradingagents_quick_think_model: str = Form(...),
    _: None = Depends(_require_same_origin),
):
    if discovery_slots_per_cycle < 0:
        raise HTTPException(status_code=422, detail="discovery_slots_per_cycle must be >= 0")
    if watchdog_check_interval_minutes <= 0:
        raise HTTPException(status_code=422, detail="watchdog_check_interval_minutes must be > 0")
    for cron_value, field in ((pre_market_cron, "pre_market_cron"), (midday_cron, "midday_cron")):
        try:
            CronTrigger.from_crontab(cron_value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid {field}: {exc}") from exc
    if not tradingagents_deep_think_model.strip() or not tradingagents_quick_think_model.strip():
        raise HTTPException(status_code=422, detail="model names must not be empty")

    config_editing.write_env_values(_ENV_PATH, {
        "DISCOVERY_SLOTS_PER_CYCLE": str(discovery_slots_per_cycle),
        "WATCHDOG_CHECK_INTERVAL_MINUTES": str(watchdog_check_interval_minutes),
        "PRE_MARKET_CRON": pre_market_cron,
        "MIDDAY_CRON": midday_cron,
        "TRADINGAGENTS_DEEP_THINK_MODEL": tradingagents_deep_think_model,
        "TRADINGAGENTS_QUICK_THINK_MODEL": tradingagents_quick_think_model,
    })
    return RedirectResponse("/config", status_code=303)


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
            "run_once_log": _tail_log("run_once"),
        },
    )


@app.post("/control/{name}/start")
def start_process(name: str, _: None = Depends(_require_same_origin)):
    if name not in ("scheduler", "watchdog"):
        raise HTTPException(status_code=404, detail="Unknown process")

    with _start_lock:
        if process_control.get_process_status(name).alive:
            raise HTTPException(status_code=409, detail=f"{name} is already running")

        process_control.clear_stop_request(name)
        process_control.spawn_detached(name)
        for _ in range(_START_POLL_ATTEMPTS):
            time.sleep(_START_POLL_INTERVAL_SECONDS)
            status = process_control.get_process_status(name)
            if status.alive:
                return {"started": True, "pid": status.pid}

    return {"started": True, "pid": None, "note": "spawned but not yet confirmed alive — refresh shortly"}


@app.post("/control/{name}/stop")
def stop_process(name: str, _: None = Depends(_require_same_origin)):
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

    return {
        "stopped": False,
        "forced": False,
        "note": (
            f"did not stop within {_STOP_TIMEOUT_SECONDS}s — it may still be finishing an "
            "in-flight research cycle. Use Force Stop only if you're sure it's safe to kill "
            "(a forced kill mid-order-submission can leave an order live at the broker with "
            "no local record)."
        ),
    }


@app.post("/control/{name}/force-stop")
def force_stop_process(name: str, _: None = Depends(_require_same_origin)):
    """Deliberate, separate action from /stop — never triggered automatically.
    A graceful stop can time out while a research cycle is still in flight
    (measured ~22 min/ticker); force-killing then can orphan an order already
    submitted to Alpaca but not yet journaled, so this is never automatic."""
    if name not in ("scheduler", "watchdog"):
        raise HTTPException(status_code=404, detail="Unknown process")

    status = process_control.get_process_status(name)
    if not status.alive:
        process_control.clear_stop_request(name)
        return {"stopped": True, "forced": False, "note": "was not running"}

    process_control.force_kill(status.pid)
    process_control.remove_pidfile(name)
    process_control.clear_stop_request(name)
    return {"stopped": True, "forced": True}


@app.post("/control/run-now")
def run_now(_: None = Depends(_require_same_origin)):
    pid = process_control.spawn_detached("run_once")
    return {"started": True, "pid": pid}
