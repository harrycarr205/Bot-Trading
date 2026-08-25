"""Deterministic, non-LLM order validation — ARCHITECTURE.md §4/§8.

Pure functions, no I/O. This is the only code path allowed to approve an order for
submission to Alpaca; it never trusts the agent's own account of its risk math and
fails closed (rejects) on any single failed check or ambiguous/malformed input.
"""

from __future__ import annotations

import dataclasses
import datetime


@dataclasses.dataclass(frozen=True)
class CheckResult:
    passed: bool
    reason: str = ""


@dataclasses.dataclass(frozen=True)
class OrderProposal:
    ticker: str
    side: str  # "buy" | "sell"
    order_type: str  # must be "limit"
    qty: float
    limit_price: float
    data_timestamp: datetime.datetime


@dataclasses.dataclass(frozen=True)
class PortfolioState:
    equity: float
    cash: float
    position_value_by_ticker: dict[str, float]
    open_order_tickers_and_sides: set[tuple[str, str]]


@dataclasses.dataclass(frozen=True)
class OrderValidationResult:
    approved: bool
    checks: list[tuple[str, CheckResult]]

    @property
    def rejection_reasons(self) -> list[str]:
        return [name for name, result in self.checks if not result.passed]


def check_market_status(market_status: str) -> CheckResult:
    if market_status != "open":
        return CheckResult(False, f"market status is '{market_status}', not 'open'")
    return CheckResult(True)


def check_order_type(order_type: str) -> CheckResult:
    if order_type != "limit":
        return CheckResult(False, f"order_type '{order_type}' is not 'limit' — market orders are never allowed")
    return CheckResult(True)


def check_position_size(
    proposal: OrderProposal, portfolio: PortfolioState, max_position_pct: float
) -> CheckResult:
    if proposal.side != "buy":
        return CheckResult(True)
    if portfolio.equity <= 0:
        return CheckResult(False, "portfolio equity is zero or negative")
    order_notional = proposal.qty * proposal.limit_price
    existing = portfolio.position_value_by_ticker.get(proposal.ticker, 0.0)
    resulting_pct = (existing + order_notional) / portfolio.equity
    if resulting_pct > max_position_pct:
        return CheckResult(
            False,
            f"resulting position {resulting_pct:.2%} of equity exceeds max_position_pct {max_position_pct:.2%}",
        )
    return CheckResult(True)


def check_cash_reserve(
    proposal: OrderProposal, portfolio: PortfolioState, cash_reserve_pct: float
) -> CheckResult:
    if proposal.side != "buy":
        return CheckResult(True)
    order_notional = proposal.qty * proposal.limit_price
    cash_after = portfolio.cash - order_notional
    min_required_cash = cash_reserve_pct * portfolio.equity
    if cash_after < min_required_cash:
        return CheckResult(
            False,
            f"cash after order ({cash_after:.2f}) would fall below required reserve ({min_required_cash:.2f})",
        )
    return CheckResult(True)


def check_exposure(
    proposal: OrderProposal, portfolio: PortfolioState, cash_reserve_pct: float
) -> CheckResult:
    if proposal.side != "buy" or portfolio.equity <= 0:
        return CheckResult(True)
    order_notional = proposal.qty * proposal.limit_price
    total_invested = sum(portfolio.position_value_by_ticker.values())
    resulting_invested_pct = (total_invested + order_notional) / portfolio.equity
    max_invested_pct = 1.0 - cash_reserve_pct
    if resulting_invested_pct > max_invested_pct:
        return CheckResult(
            False,
            f"resulting total exposure {resulting_invested_pct:.2%} exceeds cap {max_invested_pct:.2%}"
            f" (1 - cash_reserve_pct)",
        )
    return CheckResult(True)


def check_stale_data(
    proposal: OrderProposal, now: datetime.datetime, stale_data_max_age_minutes: int
) -> CheckResult:
    age = now - proposal.data_timestamp
    max_age = datetime.timedelta(minutes=stale_data_max_age_minutes)
    if age > max_age:
        return CheckResult(False, f"underlying data is {age} old, exceeds max age {max_age}")
    return CheckResult(True)


def check_duplicate_order(proposal: OrderProposal, portfolio: PortfolioState) -> CheckResult:
    key = (proposal.ticker, proposal.side)
    if key in portfolio.open_order_tickers_and_sides:
        return CheckResult(False, f"an open order already exists for {proposal.ticker} {proposal.side}")
    return CheckResult(True)


def validate_order(
    proposal: OrderProposal,
    portfolio: PortfolioState,
    market_status: str,
    now: datetime.datetime,
    max_position_pct: float,
    cash_reserve_pct: float,
    stale_data_max_age_minutes: int,
) -> OrderValidationResult:
    """Aggregate all checks. Approved only if every single check passes — fails closed."""
    checks = [
        ("market_status", check_market_status(market_status)),
        ("order_type", check_order_type(proposal.order_type)),
        ("position_size", check_position_size(proposal, portfolio, max_position_pct)),
        ("cash_reserve", check_cash_reserve(proposal, portfolio, cash_reserve_pct)),
        ("exposure", check_exposure(proposal, portfolio, cash_reserve_pct)),
        ("stale_data", check_stale_data(proposal, now, stale_data_max_age_minutes)),
        ("duplicate_order", check_duplicate_order(proposal, portfolio)),
    ]
    approved = all(result.passed for _, result in checks)
    return OrderValidationResult(approved=approved, checks=checks)
