import pytest

from tradingsystem.risk.kelly_sizing import compute_half_kelly_target_fraction, compute_kelly_fraction


def test_compute_kelly_fraction_positive_edge():
    # f* = (0.6*2.0 - 0.4) / 2.0 = 0.8 / 2.0 = 0.4
    assert compute_kelly_fraction(win_rate=0.6, payoff_ratio=2.0) == pytest.approx(0.4)


def test_compute_kelly_fraction_negative_edge_clamps_to_zero():
    # f* = (0.3*1.0 - 0.7) / 1.0 = -0.4 -> clamped
    assert compute_kelly_fraction(win_rate=0.3, payoff_ratio=1.0) == 0.0


def test_compute_kelly_fraction_zero_payoff_ratio_returns_zero():
    assert compute_kelly_fraction(win_rate=0.6, payoff_ratio=0.0) == 0.0


def test_compute_half_kelly_insufficient_sample_returns_none():
    returns_pct = [0.10, -0.05, 0.10]  # only 3 trades
    assert compute_half_kelly_target_fraction(returns_pct, min_sample_size=10) is None


def test_compute_half_kelly_all_wins_returns_none():
    # Payoff ratio is undefined with no losses to divide by.
    returns_pct = [0.10] * 10
    assert compute_half_kelly_target_fraction(returns_pct, min_sample_size=10) is None


def test_compute_half_kelly_all_losses_returns_none():
    returns_pct = [-0.05] * 10
    assert compute_half_kelly_target_fraction(returns_pct, min_sample_size=10) is None


def test_compute_half_kelly_normal_case():
    # 6 wins @ +10%, 4 losses @ -5%: win_rate=0.6, avg_win=0.10, avg_loss=0.05,
    # payoff_ratio=2.0 -> full Kelly 0.4 -> half Kelly 0.2
    returns_pct = [0.10] * 6 + [-0.05] * 4
    assert compute_half_kelly_target_fraction(returns_pct, min_sample_size=10) == pytest.approx(0.2)


def test_compute_half_kelly_clamped_to_max_fraction():
    returns_pct = [0.10] * 6 + [-0.05] * 4  # half-Kelly would be 0.2
    assert compute_half_kelly_target_fraction(returns_pct, min_sample_size=10, max_fraction=0.1) == 0.1
