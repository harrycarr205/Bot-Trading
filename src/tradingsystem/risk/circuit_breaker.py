"""Pure drawdown-% calculation and daily/weekly circuit-breaker trip check.

Kept DB-free so the trip logic itself is fully unit-testable. Persisting/reading
trip state lives in db/repositories.py, used by the orchestration layer.
"""

from __future__ import annotations

import dataclasses


def compute_drawdown_pct(peak_equity: float, current_equity: float) -> float:
    if peak_equity <= 0:
        return 0.0
    return max(0.0, (peak_equity - current_equity) / peak_equity)


@dataclasses.dataclass(frozen=True)
class BreakerCheckResult:
    tripped: bool
    breaker_type: str
    drawdown_pct: float
    threshold_pct: float


def check_daily_breaker(
    day_start_equity: float, current_equity: float, threshold_pct: float
) -> BreakerCheckResult:
    drawdown = compute_drawdown_pct(day_start_equity, current_equity)
    return BreakerCheckResult(
        tripped=drawdown > threshold_pct,
        breaker_type="daily",
        drawdown_pct=drawdown,
        threshold_pct=threshold_pct,
    )


def check_weekly_breaker(
    week_start_equity: float, current_equity: float, threshold_pct: float
) -> BreakerCheckResult:
    drawdown = compute_drawdown_pct(week_start_equity, current_equity)
    return BreakerCheckResult(
        tripped=drawdown > threshold_pct,
        breaker_type="weekly",
        drawdown_pct=drawdown,
        threshold_pct=threshold_pct,
    )
