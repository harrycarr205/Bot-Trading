"""Composes the decision-engine, risk, and execution modules into one scheduled
cycle — ARCHITECTURE.md §2/§6. Framework-free (no APScheduler import here) so it's
directly unit-testable; scheduler.py is the only caller that wires this to cron.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import zoneinfo

from sqlalchemy.orm import Session

from tradingsystem.config import RiskConfig, Settings, load_risk_config, load_watchlist
from tradingsystem.db.models import AgentRun, PortfolioSnapshot
from tradingsystem.db.repositories import get_active_breaker_event, record_breaker_trip
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol
from tradingsystem.orchestration import discord_alerts, heartbeat
from tradingsystem.risk.circuit_breaker import check_daily_breaker, check_weekly_breaker

log = logging.getLogger(__name__)

_NY = zoneinfo.ZoneInfo("America/New_York")


def _ny_today() -> datetime.date:
    return datetime.datetime.now(_NY).date()


def _ny_monday_of_this_week(today: datetime.date) -> datetime.date:
    return today - datetime.timedelta(days=today.weekday())


def _journal_skip(session: Session, watchlist: list[str], run_type: str, market_status: str, outcome: str) -> None:
    now = datetime.datetime.utcnow()
    for ticker in watchlist:
        session.add(AgentRun(
            ticker=ticker, run_type=run_type, started_at=now, finished_at=now,
            market_status=market_status, outcome=outcome,
        ))
    session.commit()


def ensure_snapshot_baseline(session: Session, alpaca_client: AlpacaClientProtocol) -> None:
    today = _ny_today()
    existing = session.query(PortfolioSnapshot).filter_by(snapshot_date=today).one_or_none()
    if existing is not None:
        return
    account = alpaca_client.get_account()
    session.add(PortfolioSnapshot(
        snapshot_date=today, equity=account.equity, cash=account.cash,
        positions=alpaca_client.get_positions(),
    ))
    session.flush()


@dataclasses.dataclass(frozen=True)
class BreakerGateResult:
    blocked: bool
    tripped_types: list[str]


def check_breakers(
    session: Session, alpaca_client: AlpacaClientProtocol, risk_config: RiskConfig, settings: Settings
) -> BreakerGateResult:
    today = _ny_today()
    monday = _ny_monday_of_this_week(today)

    day_start = session.query(PortfolioSnapshot).filter_by(snapshot_date=today).one()
    week_start = (
        session.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.snapshot_date >= monday)
        .order_by(PortfolioSnapshot.snapshot_date.asc())
        .first()
    )
    current_equity = alpaca_client.get_account().equity

    daily = check_daily_breaker(float(day_start.equity), current_equity, risk_config.daily_drawdown_breaker_pct)
    weekly = check_weekly_breaker(float(week_start.equity), current_equity, risk_config.weekly_drawdown_breaker_pct)

    for result in (daily, weekly):
        if result.tripped and get_active_breaker_event(session, result.breaker_type) is None:
            record_breaker_trip(
                session, result.breaker_type,
                trigger_reason=(
                    f"{result.breaker_type} drawdown {result.drawdown_pct:.4f} "
                    f"exceeded threshold {result.threshold_pct:.4f}"
                ),
            )
            discord_alerts.send_alert(
                settings,
                f"CIRCUIT BREAKER TRIPPED: {result.breaker_type} drawdown "
                f"{result.drawdown_pct:.2%} exceeds {result.threshold_pct:.2%}",
                level="critical",
            )

    tripped_types = []
    if daily.tripped or get_active_breaker_event(session, "daily") is not None:
        tripped_types.append("daily")
    if weekly.tripped or get_active_breaker_event(session, "weekly") is not None:
        tripped_types.append("weekly")

    return BreakerGateResult(blocked=bool(tripped_types), tripped_types=tripped_types)


def run_full_cycle(
    session: Session,
    alpaca_client: AlpacaClientProtocol,
    run_type: str,
    settings: Settings | None = None,
    risk_config: RiskConfig | None = None,
    watchlist: list[str] | None = None,
) -> None:
    settings = settings or Settings()
    risk_config = risk_config or load_risk_config()
    watchlist = watchlist if watchlist is not None else load_watchlist().tickers

    heartbeat.record_heartbeat(session, run_type)
    session.commit()

    market_status = alpaca_client.get_clock()
    if market_status != "open":
        _journal_skip(session, watchlist, run_type, market_status, outcome="market_closed")
        return

    ensure_snapshot_baseline(session, alpaca_client)
    session.commit()

    gate = check_breakers(session, alpaca_client, risk_config, settings)
    session.commit()
    if gate.blocked:
        _journal_skip(session, watchlist, run_type, market_status, outcome="circuit_breaker_active")
        return

    # Per-ticker research/trade loop — Task 5.

    heartbeat.record_heartbeat(session, run_type)
    session.commit()
