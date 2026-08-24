import datetime

from tradingsystem.risk.position_sizing import size_order
from tradingsystem.risk.validation import PortfolioState

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def make_portfolio(**overrides) -> PortfolioState:
    defaults = dict(
        equity=100_000.0,
        cash=80_000.0,
        position_value_by_ticker={},
        open_order_tickers_and_sides=set(),
    )
    defaults.update(overrides)
    return PortfolioState(**defaults)


def test_hold_never_orders():
    portfolio = make_portfolio(position_value_by_ticker={"AAPL": 5_000.0})
    result = size_order("Hold", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW)
    assert result is None


def test_buy_with_no_position_tops_up_to_full_target():
    portfolio = make_portfolio()
    result = size_order("Buy", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW)
    # target = 10% of 100,000 = 10,000 -> 100 shares at $100
    assert result is not None
    assert result.side == "buy"
    assert result.qty == 100


def test_buy_already_at_target_does_nothing():
    portfolio = make_portfolio(position_value_by_ticker={"AAPL": 10_000.0})
    result = size_order("Buy", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW)
    assert result is None


def test_buy_partial_position_tops_up_the_remainder():
    portfolio = make_portfolio(position_value_by_ticker={"AAPL": 4_000.0})
    result = size_order("Buy", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW)
    # need 10,000 - 4,000 = 6,000 -> 60 shares
    assert result is not None
    assert result.qty == 60


def test_overweight_with_no_position_tops_up_to_half_target():
    portfolio = make_portfolio()
    result = size_order(
        "Overweight", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW
    )
    # target = 5% of 100,000 = 5,000 -> 50 shares
    assert result is not None
    assert result.side == "buy"
    assert result.qty == 50


def test_overweight_already_at_half_target_does_nothing():
    portfolio = make_portfolio(position_value_by_ticker={"AAPL": 5_000.0})
    result = size_order(
        "Overweight", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW
    )
    assert result is None


def test_underweight_with_no_position_does_nothing():
    portfolio = make_portfolio()
    result = size_order(
        "Underweight", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW
    )
    assert result is None


def test_underweight_reduces_to_half_current_position():
    portfolio = make_portfolio(position_value_by_ticker={"AAPL": 10_000.0})
    result = size_order(
        "Underweight", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW
    )
    # reduce by 5,000 -> 50 shares, sell side
    assert result is not None
    assert result.side == "sell"
    assert result.qty == 50


def test_sell_with_no_position_does_nothing():
    portfolio = make_portfolio()
    result = size_order("Sell", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW)
    assert result is None


def test_sell_liquidates_entire_position():
    portfolio = make_portfolio(position_value_by_ticker={"AAPL": 10_000.0})
    result = size_order("Sell", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW)
    assert result is not None
    assert result.side == "sell"
    assert result.qty == 100


def test_qty_rounding_to_zero_returns_none():
    portfolio = make_portfolio()
    # need_notional = 10,000 but price so high that qty floors to 0
    result = size_order(
        "Buy", "AAPL", portfolio, current_price=50_000.0, max_position_pct=0.10, data_timestamp=NOW
    )
    assert result is None
