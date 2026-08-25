"""Ties risk validation + kill switch + Alpaca submission + DB persistence together.

This is the only code path allowed to call AlpacaClient.submit_limit_order.
ARCHITECTURE.md §4: never trust the agent's own risk math — portfolio state is
always re-derived live from Alpaca, right before validating.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import uuid

from alpaca.common.exceptions import APIError
from sqlalchemy.orm import Session

from tradingsystem.db.models import Fill, Order
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol
from tradingsystem.risk.kill_switch import is_kill_switch_active
from tradingsystem.risk.validation import (
    OrderProposal,
    PortfolioState,
    validate_order,
)

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ExecutionResult:
    submitted: bool
    alpaca_order_id: str | None = None
    rejection_reasons: list[str] = dataclasses.field(default_factory=list)
    error: str | None = None


def build_portfolio_state(client: AlpacaClientProtocol) -> PortfolioState:
    account = client.get_account()
    return PortfolioState(
        equity=account.equity,
        cash=account.cash,
        position_value_by_ticker=client.get_positions(),
        open_order_tickers_and_sides=client.get_open_orders(),
    )


def place_order(
    session: Session,
    client: AlpacaClientProtocol,
    proposal: OrderProposal,
    decision_id: uuid.UUID,
    kill_switch_file: str,
    max_position_pct: float,
    cash_reserve_pct: float,
    stale_data_max_age_minutes: int,
    now: datetime.datetime | None = None,
) -> ExecutionResult:
    if is_kill_switch_active(kill_switch_file):
        log.critical("kill switch active — rejecting order for %s without contacting Alpaca", proposal.ticker)
        return ExecutionResult(submitted=False, rejection_reasons=["kill_switch_active"])

    portfolio = build_portfolio_state(client)
    market_status = client.get_clock()

    result = validate_order(
        proposal,
        portfolio,
        market_status=market_status,
        now=now or datetime.datetime.utcnow(),
        max_position_pct=max_position_pct,
        cash_reserve_pct=cash_reserve_pct,
        stale_data_max_age_minutes=stale_data_max_age_minutes,
    )
    if not result.approved:
        log.warning("order rejected for %s: %s", proposal.ticker, result.rejection_reasons)
        return ExecutionResult(submitted=False, rejection_reasons=result.rejection_reasons)

    try:
        submitted = client.submit_limit_order(
            ticker=proposal.ticker,
            side=proposal.side,
            qty=proposal.qty,
            limit_price=proposal.limit_price,
        )
    except APIError as exc:
        log.error("Alpaca API error submitting order for %s: %s", proposal.ticker, exc)
        return ExecutionResult(submitted=False, error=str(exc))

    order = Order(
        decision_id=decision_id,
        ticker=proposal.ticker,
        side=proposal.side,
        qty=proposal.qty,
        limit_price=proposal.limit_price,
        status=submitted.status,
        alpaca_order_id=submitted.alpaca_order_id,
    )
    session.add(order)
    session.flush()

    return ExecutionResult(submitted=True, alpaca_order_id=submitted.alpaca_order_id)


_TERMINAL_ORDER_STATUSES = {"filled", "canceled", "expired", "rejected"}


def sync_all_open_orders(session: Session, client: AlpacaClientProtocol) -> None:
    """Poll and persist fill updates for every order not yet in a terminal state.

    Called once per orchestration cycle (orchestration/cycle.py) — not scheduled
    independently, since the twice-daily cadence is frequent enough to keep
    order/fill/P&L data reasonably current.
    """
    open_orders = session.query(Order).filter(Order.status.notin_(_TERMINAL_ORDER_STATUSES)).all()
    for order in open_orders:
        sync_order_fills(session, client, order)


def sync_order_fills(session: Session, client: AlpacaClientProtocol, order: Order) -> None:
    """Poll Alpaca for this order's current status and persist any new fill(s)."""
    status = client.get_order(order.alpaca_order_id)
    order.status = status.status

    if status.status == "partially_filled":
        log.warning("partial fill for order %s (%s): %s of requested qty", order.id, order.ticker, status.filled_qty)

    if status.filled_qty and status.filled_at is not None:
        already_recorded = sum(f.fill_qty for f in order.fills)
        new_qty = status.filled_qty - already_recorded
        if new_qty > 0:
            fill = Fill(
                fill_price=status.filled_avg_price or 0.0,
                fill_qty=new_qty,
                filled_at=status.filled_at,
            )
            order.fills.append(fill)
            session.flush()
