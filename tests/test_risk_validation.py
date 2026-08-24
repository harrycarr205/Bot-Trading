import datetime

import pytest

from tradingsystem.risk.validation import (
    OrderProposal,
    PortfolioState,
    check_cash_reserve,
    check_duplicate_order,
    check_exposure,
    check_market_status,
    check_order_type,
    check_position_size,
    check_stale_data,
    validate_order,
)

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def make_proposal(**overrides) -> OrderProposal:
    defaults = dict(
        ticker="AAPL",
        side="buy",
        order_type="limit",
        qty=10,
        limit_price=100.0,
        data_timestamp=NOW,
    )
    defaults.update(overrides)
    return OrderProposal(**defaults)


def make_portfolio(**overrides) -> PortfolioState:
    defaults = dict(
        equity=100_000.0,
        cash=80_000.0,
        position_value_by_ticker={},
        open_order_tickers_and_sides=set(),
    )
    defaults.update(overrides)
    return PortfolioState(**defaults)


def test_market_status_open_passes():
    assert check_market_status("open").passed


def test_market_status_closed_fails():
    assert not check_market_status("closed").passed


def test_order_type_limit_passes():
    assert check_order_type("limit").passed


def test_order_type_market_fails():
    assert not check_order_type("market").passed


def test_position_size_within_limit_passes():
    proposal = make_proposal(qty=10, limit_price=100.0)  # $1,000 of $100,000 = 1%
    portfolio = make_portfolio()
    assert check_position_size(proposal, portfolio, max_position_pct=0.10).passed


def test_position_size_over_limit_fails():
    proposal = make_proposal(qty=200, limit_price=100.0)  # $20,000 of $100,000 = 20%
    portfolio = make_portfolio()
    assert not check_position_size(proposal, portfolio, max_position_pct=0.10).passed


def test_position_size_zero_equity_fails_closed():
    proposal = make_proposal()
    portfolio = make_portfolio(equity=0.0)
    assert not check_position_size(proposal, portfolio, max_position_pct=0.10).passed


def test_cash_reserve_respected_passes():
    proposal = make_proposal(qty=10, limit_price=100.0)  # $1,000 order
    portfolio = make_portfolio(equity=100_000.0, cash=80_000.0)
    assert check_cash_reserve(proposal, portfolio, cash_reserve_pct=0.20).passed


def test_cash_reserve_breached_fails():
    proposal = make_proposal(qty=1000, limit_price=100.0)  # $100,000 order
    portfolio = make_portfolio(equity=100_000.0, cash=80_000.0)
    assert not check_cash_reserve(proposal, portfolio, cash_reserve_pct=0.20).passed


def test_cash_reserve_sell_side_always_passes():
    proposal = make_proposal(side="sell", qty=1000, limit_price=100.0)
    portfolio = make_portfolio(equity=100_000.0, cash=0.0)
    assert check_cash_reserve(proposal, portfolio, cash_reserve_pct=0.20).passed


def test_exposure_within_cap_passes():
    proposal = make_proposal(qty=10, limit_price=100.0)
    portfolio = make_portfolio(equity=100_000.0, position_value_by_ticker={"MSFT": 10_000.0})
    assert check_exposure(proposal, portfolio, cash_reserve_pct=0.20).passed


def test_exposure_over_cap_fails():
    proposal = make_proposal(qty=100, limit_price=100.0)  # $10,000 more
    portfolio = make_portfolio(equity=100_000.0, position_value_by_ticker={"MSFT": 75_000.0})
    assert not check_exposure(proposal, portfolio, cash_reserve_pct=0.20).passed


def test_stale_data_fresh_passes():
    proposal = make_proposal(data_timestamp=NOW - datetime.timedelta(minutes=5))
    assert check_stale_data(proposal, now=NOW, stale_data_max_age_minutes=15).passed


def test_stale_data_too_old_fails():
    proposal = make_proposal(data_timestamp=NOW - datetime.timedelta(minutes=30))
    assert not check_stale_data(proposal, now=NOW, stale_data_max_age_minutes=15).passed


def test_duplicate_order_none_open_passes():
    proposal = make_proposal()
    portfolio = make_portfolio()
    assert check_duplicate_order(proposal, portfolio).passed


def test_duplicate_order_existing_open_fails():
    proposal = make_proposal(ticker="AAPL", side="buy")
    portfolio = make_portfolio(open_order_tickers_and_sides={("AAPL", "buy")})
    assert not check_duplicate_order(proposal, portfolio).passed


def test_validate_order_all_pass_is_approved():
    proposal = make_proposal()
    portfolio = make_portfolio()
    result = validate_order(
        proposal,
        portfolio,
        market_status="open",
        now=NOW,
        max_position_pct=0.10,
        cash_reserve_pct=0.20,
        stale_data_max_age_minutes=15,
    )
    assert result.approved
    assert result.rejection_reasons == []


def test_validate_order_single_failure_fails_closed():
    proposal = make_proposal(order_type="market")
    portfolio = make_portfolio()
    result = validate_order(
        proposal,
        portfolio,
        market_status="open",
        now=NOW,
        max_position_pct=0.10,
        cash_reserve_pct=0.20,
        stale_data_max_age_minutes=15,
    )
    assert not result.approved
    assert "order_type" in result.rejection_reasons


def test_validate_order_market_closed_fails_closed():
    proposal = make_proposal()
    portfolio = make_portfolio()
    result = validate_order(
        proposal,
        portfolio,
        market_status="closed",
        now=NOW,
        max_position_pct=0.10,
        cash_reserve_pct=0.20,
        stale_data_max_age_minutes=15,
    )
    assert not result.approved
    assert "market_status" in result.rejection_reasons
