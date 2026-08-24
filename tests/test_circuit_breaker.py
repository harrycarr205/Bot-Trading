from tradingsystem.risk.circuit_breaker import (
    check_daily_breaker,
    check_weekly_breaker,
    compute_drawdown_pct,
)


def test_compute_drawdown_pct_no_drop():
    assert compute_drawdown_pct(100_000, 100_000) == 0.0


def test_compute_drawdown_pct_gain_clamped_to_zero():
    assert compute_drawdown_pct(100_000, 110_000) == 0.0


def test_compute_drawdown_pct_normal_drop():
    assert compute_drawdown_pct(100_000, 95_000) == 0.05


def test_compute_drawdown_pct_zero_peak_is_safe():
    assert compute_drawdown_pct(0, 0) == 0.0


def test_daily_breaker_under_threshold_not_tripped():
    result = check_daily_breaker(day_start_equity=100_000, current_equity=98_500, threshold_pct=0.03)
    assert not result.tripped
    assert result.breaker_type == "daily"


def test_daily_breaker_over_threshold_tripped():
    result = check_daily_breaker(day_start_equity=100_000, current_equity=96_000, threshold_pct=0.03)
    assert result.tripped


def test_daily_breaker_exactly_at_threshold_not_tripped():
    # Strictly greater-than, not >=, so an exact-threshold drawdown doesn't trip.
    result = check_daily_breaker(day_start_equity=100_000, current_equity=97_000, threshold_pct=0.03)
    assert not result.tripped


def test_weekly_breaker_under_threshold_not_tripped():
    result = check_weekly_breaker(week_start_equity=100_000, current_equity=93_000, threshold_pct=0.08)
    assert not result.tripped
    assert result.breaker_type == "weekly"


def test_weekly_breaker_over_threshold_tripped():
    result = check_weekly_breaker(week_start_equity=100_000, current_equity=90_000, threshold_pct=0.08)
    assert result.tripped
