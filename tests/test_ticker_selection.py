from tradingsystem.execution.alpaca_client import DailyBars
from tradingsystem.orchestration.ticker_selection import (
    compute_earnings_proximity_score,
    compute_momentum_pct,
    compute_relative_volume,
    rank_candidates,
    select_tickers_for_cycle,
)


def test_compute_momentum_pct_positive():
    assert compute_momentum_pct([100.0, 105.0, 110.0]) == 0.1


def test_compute_momentum_pct_negative():
    assert compute_momentum_pct([100.0, 95.0, 90.0]) == -0.1


def test_compute_momentum_pct_insufficient_data_returns_zero():
    assert compute_momentum_pct([100.0]) == 0.0
    assert compute_momentum_pct([]) == 0.0


def test_compute_relative_volume_spike():
    # baseline avg = (100+100+100)/3 = 100, most recent = 300 -> 3.0x
    assert compute_relative_volume([100.0, 100.0, 100.0, 300.0]) == 3.0


def test_compute_relative_volume_insufficient_data_returns_neutral():
    assert compute_relative_volume([100.0]) == 1.0
    assert compute_relative_volume([]) == 1.0


def test_compute_earnings_proximity_score_near_term_returns_days_until():
    assert compute_earnings_proximity_score(3) == 3.0
    assert compute_earnings_proximity_score(0) == 0.0


def test_compute_earnings_proximity_score_none_returns_neutral_baseline():
    assert compute_earnings_proximity_score(None, horizon_days=10) == 10.0


def test_compute_earnings_proximity_score_beyond_horizon_returns_neutral_baseline():
    assert compute_earnings_proximity_score(45, horizon_days=10) == 10.0


def test_compute_earnings_proximity_score_negative_returns_neutral_baseline():
    # A stale/already-passed date the calendar hasn't refreshed yet — treat as
    # no usable signal rather than ranking it as maximally "close."
    assert compute_earnings_proximity_score(-2, horizon_days=10) == 10.0


def test_rank_candidates_orders_best_first():
    bars = {
        "AAPL": DailyBars(ticker="AAPL", closes=[100.0, 110.0], volumes=[100.0, 100.0]),  # strong momentum, flat volume
        "MSFT": DailyBars(ticker="MSFT", closes=[100.0, 100.0], volumes=[100.0, 100.0]),  # flat momentum, flat volume
        "NVDA": DailyBars(ticker="NVDA", closes=[100.0, 90.0], volumes=[100.0, 100.0]),   # negative momentum, flat volume
    }
    ranked = rank_candidates(bars)
    assert ranked == ["AAPL", "MSFT", "NVDA"]


def test_rank_candidates_ties_break_alphabetically():
    bars = {
        "ZETA": DailyBars(ticker="ZETA", closes=[100.0, 100.0], volumes=[100.0, 100.0]),
        "ALPHA": DailyBars(ticker="ALPHA", closes=[100.0, 100.0], volumes=[100.0, 100.0]),
    }
    ranked = rank_candidates(bars)
    assert ranked == ["ALPHA", "ZETA"]


def test_select_tickers_for_cycle_held_positions_are_uncapped():
    held = {"AAPL", "MSFT", "GOOGL", "META", "NVDA"}  # 5 held, more than discovery_slots
    ranked = ["XOM", "CVX"]
    result = select_tickers_for_cycle(held, ranked, discovery_slots=2)
    assert result == sorted(held | {"XOM", "CVX"})


def test_select_tickers_for_cycle_discovery_excludes_already_held():
    held = {"AAPL"}
    ranked = ["AAPL", "MSFT", "GOOGL"]  # AAPL is top-ranked but already held
    result = select_tickers_for_cycle(held, ranked, discovery_slots=1)
    assert result == sorted({"AAPL", "MSFT"})  # MSFT is the next-best not-held candidate


def test_select_tickers_for_cycle_zero_discovery_slots():
    held = {"AAPL"}
    ranked = ["MSFT", "GOOGL"]
    result = select_tickers_for_cycle(held, ranked, discovery_slots=0)
    assert result == ["AAPL"]


from tradingsystem.execution.alpaca_client import PositionDetail
from tradingsystem.orchestration.ticker_selection import build_cycle_ticker_list


class FakeAlpacaClientForSelection:
    def __init__(self, positions=None, bars=None):
        self.positions = positions or []
        self.bars = bars or {}

    def get_position_details(self):
        return self.positions

    def get_recent_daily_bars(self, tickers, lookback_days):
        return {t: self.bars[t] for t in tickers if t in self.bars}


def test_build_cycle_ticker_list_combines_held_and_discovery():
    client = FakeAlpacaClientForSelection(
        positions=[PositionDetail(ticker="AAPL", qty=10, avg_entry_price=100.0, current_price=110.0)],
        bars={
            "MSFT": DailyBars(ticker="MSFT", closes=[100.0, 120.0], volumes=[100.0, 100.0]),  # strong momentum
            "NVDA": DailyBars(ticker="NVDA", closes=[100.0, 90.0], volumes=[100.0, 100.0]),   # weak momentum
        },
    )

    result = build_cycle_ticker_list(client, ["MSFT", "NVDA"], discovery_slots=1)

    assert result == ["AAPL", "MSFT"]  # AAPL held (uncapped); MSFT is the top-ranked discovery pick
