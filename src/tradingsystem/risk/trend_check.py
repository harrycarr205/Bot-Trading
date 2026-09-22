"""Independent trend cross-check: a moving-average crossover that is
genuinely independent of the LLM's own reasoning, shadow-logged against
each decision's rating -- docs/superpowers/specs/2026-09-22-trend-cross-check-design.md.

Pure, no I/O -- same style as risk/kelly_sizing.py and risk/stop_loss.py.
Deliberately a different window (9/50-day close) from the 21-day momentum
signal in orchestration/ticker_selection.py, which already influences
which tickers get selected for discovery -- reusing it here would partly
be checking the LLM's rating against a signal that helped choose the
ticker in the first place.
"""

from __future__ import annotations

_BULLISH_RATINGS = {"Buy", "Overweight"}
_BEARISH_RATINGS = {"Sell", "Underweight"}


def compute_moving_average(closes: list[float], period: int) -> float | None:
    """Simple moving average of the last `period` closes.

    None if fewer than `period` closes are available -- the caller
    (orchestration/cycle.py) leaves the trend fields unset in that case
    rather than estimating from a shorter window.
    """
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def classify_trend(short_ma: float, long_ma: float, deadband_pct: float = 0.003) -> str:
    """"bullish" if short_ma exceeds long_ma by more than deadband_pct of
    long_ma, "bearish" if it's below by more than deadband_pct, otherwise
    "neutral" -- long_ma is the base for the percentage since it's the
    more stable of the two lines. The deadband keeps noise right at a
    crossover from flipping the label back and forth cycle to cycle.
    """
    diff_pct = (short_ma - long_ma) / long_ma
    if diff_pct > deadband_pct:
        return "bullish"
    if diff_pct < -deadband_pct:
        return "bearish"
    return "neutral"


def trend_agrees_with_rating(rating: str, trend: str) -> bool | None:
    """True if trend's direction matches the rating's implied direction,
    False if it contradicts, None if trend is "neutral" (no directional
    call to agree or disagree with) or rating has no directional
    implication of its own (Hold, or anything not in the 5-tier set).
    """
    if trend == "neutral":
        return None
    if rating in _BULLISH_RATINGS:
        return trend == "bullish"
    if rating in _BEARISH_RATINGS:
        return trend == "bearish"
    return None
