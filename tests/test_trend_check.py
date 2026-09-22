from tradingsystem.risk.trend_check import (
    classify_trend,
    compute_moving_average,
    trend_agrees_with_rating,
)


def test_compute_moving_average_exact_window():
    assert compute_moving_average([10.0, 20.0, 30.0], period=3) == 20.0


def test_compute_moving_average_uses_only_the_last_period_closes():
    # Last 3 of [1,2,3,4,5] are [3,4,5] -> avg 4.0; the older 1,2 are ignored.
    assert compute_moving_average([1.0, 2.0, 3.0, 4.0, 5.0], period=3) == 4.0


def test_compute_moving_average_insufficient_data_returns_none():
    assert compute_moving_average([1.0, 2.0], period=5) is None


def test_classify_trend_bullish_above_deadband():
    # (110 - 100) / 100 = 0.10, well above the 0.003 default deadband.
    assert classify_trend(short_ma=110.0, long_ma=100.0) == "bullish"


def test_classify_trend_bearish_below_deadband():
    assert classify_trend(short_ma=90.0, long_ma=100.0) == "bearish"


def test_classify_trend_neutral_within_deadband():
    # (100.2 - 100) / 100 = 0.002, inside the 0.003 deadband.
    assert classify_trend(short_ma=100.2, long_ma=100.0) == "neutral"


def test_classify_trend_deadband_boundary_is_neutral():
    # Exactly at the deadband threshold (0.3%) counts as neutral, not
    # bullish -- the comparison is strictly greater-than, not >=.
    assert classify_trend(short_ma=100.3, long_ma=100.0) == "neutral"


def test_trend_agrees_with_rating_bullish_matches_buy():
    assert trend_agrees_with_rating("Buy", "bullish") is True


def test_trend_agrees_with_rating_bullish_matches_overweight():
    assert trend_agrees_with_rating("Overweight", "bullish") is True


def test_trend_agrees_with_rating_bearish_contradicts_buy():
    assert trend_agrees_with_rating("Buy", "bearish") is False


def test_trend_agrees_with_rating_bearish_matches_sell():
    assert trend_agrees_with_rating("Sell", "bearish") is True


def test_trend_agrees_with_rating_bearish_matches_underweight():
    assert trend_agrees_with_rating("Underweight", "bearish") is True


def test_trend_agrees_with_rating_bullish_contradicts_sell():
    assert trend_agrees_with_rating("Sell", "bullish") is False


def test_trend_agrees_with_rating_neutral_is_none_regardless_of_rating():
    assert trend_agrees_with_rating("Buy", "neutral") is None
    assert trend_agrees_with_rating("Sell", "neutral") is None


def test_trend_agrees_with_rating_hold_is_none():
    # Hold makes no directional claim -- never actually called with "Hold"
    # in practice (cycle.py skips Hold decisions before this runs), but
    # the function must not raise or guess if it ever is.
    assert trend_agrees_with_rating("Hold", "bullish") is None
