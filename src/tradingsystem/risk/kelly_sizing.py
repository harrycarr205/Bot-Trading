"""Fractional-Kelly position sizing from this system's own realized-trade history.

Pure, no I/O — same style as risk/stop_loss.py and risk/position_sizing.py.
Inputs (a bucket of historical % returns for one rating) are computed by
db/repositories.py's get_realized_returns_by_rating; risk/position_sizing.py
consumes this module's output in shadow mode first (see the fractional-Kelly
sizing plan's Global Constraints).
"""

from __future__ import annotations


def compute_kelly_fraction(win_rate: float, payoff_ratio: float) -> float:
    """Full Kelly fraction f* = (p*b - q) / b for a binary win/loss bet.

    p = win_rate, q = 1 - p, b = payoff_ratio (avg win size / avg loss size).
    Clamped at 0.0 (never negative) when the edge is negative — "don't
    trade" per the backlog's own framing, not a short/lever-down signal this
    long-only system supports anyway (risk/position_sizing.py).
    """
    if payoff_ratio <= 0:
        return 0.0
    loss_rate = 1.0 - win_rate
    fraction = (win_rate * payoff_ratio - loss_rate) / payoff_ratio
    return max(0.0, fraction)


def compute_half_kelly_target_fraction(
    returns_pct: list[float], min_sample_size: int, max_fraction: float = 1.0
) -> float | None:
    """Half-Kelly fraction of max_position_pct to target, from a bucket of
    historical % returns for one entry rating.

    Returns None (caller falls back to the existing flat sizing) when there
    isn't yet enough data to trust the estimate, or when every trade in the
    bucket was a win or every trade was a loss (payoff ratio undefined
    either way). Clamped to [0, max_fraction] — this fraction multiplies
    max_position_pct the same way _TARGET_FRACTION_OF_MAX does today in
    position_sizing.py, so it must never exceed the existing ceiling.
    """
    if len(returns_pct) < min_sample_size:
        return None

    wins = [r for r in returns_pct if r > 0]
    losses = [r for r in returns_pct if r <= 0]
    if not wins or not losses:
        return None

    win_rate = len(wins) / len(returns_pct)
    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    if avg_loss <= 0:
        return None
    payoff_ratio = avg_win / avg_loss

    full_kelly = compute_kelly_fraction(win_rate, payoff_ratio)
    half_kelly = full_kelly / 2.0
    return min(max_fraction, half_kelly)
