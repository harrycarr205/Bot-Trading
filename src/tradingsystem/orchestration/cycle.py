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
from tradingsystem.db.models import AgentRun, Decision, PortfolioSnapshot
from tradingsystem.db.repositories import get_active_breaker_event, record_breaker_trip
from tradingsystem.decision_engine.runner import run_research
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol
from tradingsystem.execution.executor import build_portfolio_state, place_order, sync_all_open_orders
from tradingsystem.orchestration import discord_alerts, heartbeat, memory_ingestion
from tradingsystem.risk.circuit_breaker import check_daily_breaker, check_weekly_breaker
from tradingsystem.risk.position_sizing import size_order
from tradingsystem.risk.stop_loss import is_stop_loss_triggered
from tradingsystem.risk.validation import OrderProposal

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


def check_and_execute_stop_losses(
    session: Session, alpaca_client: AlpacaClientProtocol, risk_config: RiskConfig, settings: Settings
) -> None:
    """Deterministic per-position stop-loss — ARCHITECTURE.md §4.

    Scans every live Alpaca position (not just the current watchlist — a
    ticker dropped from the watchlist while still held must still be
    protected) and force-exits any position down more than stop_loss_pct
    from its average entry price. Runs before check_breakers so a stop-loss
    still fires even while a circuit breaker is tripped: a breaker pauses
    new discretionary trading, but a stop-loss is a defensive exit, not a
    new speculative trade.

    Order.decision_id is a required FK — there is no TradingAgents decision
    behind a stop-loss sell, so a minimal AgentRun+Decision pair is
    synthesized to hold the audit trail (visible in the dashboard journal
    like any other order) without a schema change.
    """
    now = datetime.datetime.utcnow()
    for position in alpaca_client.get_position_details():
        if not is_stop_loss_triggered(position.avg_entry_price, position.current_price, risk_config.stop_loss_pct):
            continue

        drawdown_pct = (position.avg_entry_price - position.current_price) / position.avg_entry_price
        reasoning = (
            f"Stop-loss triggered: {position.ticker} down {drawdown_pct:.2%} from avg entry "
            f"${position.avg_entry_price:.2f} to ${position.current_price:.2f} "
            f"(threshold {risk_config.stop_loss_pct:.0%})"
        )

        run = AgentRun(
            ticker=position.ticker, run_type="stop_loss", started_at=now, finished_at=now,
            market_status="open", outcome="stop_loss_triggered",
        )
        session.add(run)
        session.flush()
        decision = Decision(agent_run_id=run.id, rating="Sell", decision="sell", reasoning_summary=reasoning)
        session.add(decision)
        session.flush()

        proposal = OrderProposal(
            ticker=position.ticker, side="sell", order_type="limit",
            qty=position.qty, limit_price=position.current_price, data_timestamp=now,
        )
        exec_result = place_order(
            session, alpaca_client, proposal, decision.id,
            settings.kill_switch_file, risk_config.max_position_pct,
            risk_config.cash_reserve_pct, risk_config.stale_data_max_age_minutes,
            now=now,
        )
        if exec_result.submitted:
            discord_alerts.send_alert(
                settings,
                f"STOP-LOSS: sold {proposal.qty} {position.ticker} @ {proposal.limit_price:.2f} "
                f"({reasoning}, alpaca_order_id={exec_result.alpaca_order_id})",
                level="critical",
            )
        else:
            discord_alerts.send_alert(
                settings,
                f"STOP-LOSS FAILED to execute for {position.ticker}: "
                f"{exec_result.rejection_reasons or exec_result.error} ({reasoning})",
                level="critical",
            )
        session.commit()


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

    sync_all_open_orders(session, alpaca_client)
    session.commit()

    memory_ingestion.ingest_trading_memory(session, settings)
    session.commit()

    ensure_snapshot_baseline(session, alpaca_client)
    session.commit()

    market_status = alpaca_client.get_clock()
    if market_status != "open":
        _journal_skip(session, watchlist, run_type, market_status, outcome="market_closed")
        return

    check_and_execute_stop_losses(session, alpaca_client, risk_config, settings)

    gate = check_breakers(session, alpaca_client, risk_config, settings)
    session.commit()
    if gate.blocked:
        _journal_skip(session, watchlist, run_type, market_status, outcome="circuit_breaker_active")
        return

    for ticker in watchlist:
        try:
            result = run_research(
                session, ticker, trade_date=_ny_today().isoformat(),
                market_status=market_status, run_type=run_type, settings=settings,
            )
            heartbeat.record_heartbeat(session, run_type, ticker=ticker)
            session.commit()

            if not result.ok or result.decision == "hold":
                continue

            portfolio = build_portfolio_state(alpaca_client)
            price = alpaca_client.get_latest_price(ticker)
            proposal = size_order(
                result.rating, ticker, portfolio, price,
                risk_config.max_position_pct, data_timestamp=datetime.datetime.utcnow(),
            )
            if proposal is not None:
                exec_result = place_order(
                    session, alpaca_client, proposal, result.decision_id,
                    settings.kill_switch_file, risk_config.max_position_pct,
                    risk_config.cash_reserve_pct, risk_config.stale_data_max_age_minutes,
                    now=datetime.datetime.utcnow(),
                )
                if exec_result.submitted:
                    discord_alerts.send_alert(
                        settings,
                        f"Order placed: {proposal.side} {proposal.qty} {ticker} @ "
                        f"{proposal.limit_price:.2f} (alpaca_order_id={exec_result.alpaca_order_id})",
                        level="info",
                    )
                else:
                    discord_alerts.send_alert(
                        settings,
                        f"Order rejected/failed for {ticker}: "
                        f"{exec_result.rejection_reasons or exec_result.error}",
                        level="warning",
                    )
            session.commit()
        except Exception as exc:  # noqa: BLE001 - one ticker's crash must not stop the rest of the cycle
            log.critical("unhandled error processing %s: %s", ticker, exc)
            discord_alerts.send_alert(settings, f"Cycle error on {ticker}: {exc}", level="critical")
            session.rollback()
            continue

    heartbeat.record_heartbeat(session, run_type)
    session.commit()
