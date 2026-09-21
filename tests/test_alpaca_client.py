from tradingsystem.execution.alpaca_client import DailyBars, compute_average_daily_dollar_volume, round_to_tick


def test_rounds_sub_penny_price_above_one_dollar():
    # The exact case that triggered a real Alpaca rejection in production.
    assert round_to_tick(309.565) == 309.56


def test_price_already_at_valid_tick_is_unchanged():
    assert round_to_tick(100.00) == 100.00


def test_rounds_to_four_decimals_below_one_dollar():
    assert round_to_tick(0.55555) == 0.5555


def test_rounding_can_cross_the_dollar_threshold():
    assert round_to_tick(99.995) == 100.0


def test_compute_average_daily_dollar_volume():
    bars = DailyBars(ticker="AAPL", closes=[100.0, 110.0], volumes=[1_000_000.0, 2_000_000.0])
    # (100*1,000,000 + 110*2,000,000) / 2 = (100,000,000 + 220,000,000) / 2 = 160,000,000
    assert compute_average_daily_dollar_volume(bars) == 160_000_000.0


def test_compute_average_daily_dollar_volume_empty_bars_returns_zero():
    bars = DailyBars(ticker="AAPL", closes=[], volumes=[])
    assert compute_average_daily_dollar_volume(bars) == 0.0
