"""Pure stop-loss trigger check — ARCHITECTURE.md §4: 8% fixed, per position.

Kept DB-free so the trigger logic itself is fully unit-testable, same style as
risk/circuit_breaker.py. Persisting the resulting sell and alerting live in
orchestration/cycle.py, used by the orchestration layer.
"""

from __future__ import annotations


def is_stop_loss_triggered(entry_price: float, current_price: float, stop_loss_pct: float) -> bool:
    if entry_price <= 0:
        return False
    drawdown = (entry_price - current_price) / entry_price
    return drawdown > stop_loss_pct
