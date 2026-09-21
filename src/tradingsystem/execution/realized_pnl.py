"""Average-cost-basis realized P&L for a closing/reducing sell fill.

Pure, no I/O — same style as risk/stop_loss.py and risk/circuit_breaker.py.
Persisting the result lives in execution/executor.py's sync_order_fills.
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid


@dataclasses.dataclass(frozen=True)
class FillRecord:
    side: str  # "buy" | "sell"
    price: float
    qty: float
    decision_id: uuid.UUID
    filled_at: datetime.datetime


@dataclasses.dataclass(frozen=True)
class RealizedPnlResult:
    pnl_amount: float
    decision_ids: list[uuid.UUID]
    entry_notional: float


def compute_realized_pnl(
    prior_fills: list[FillRecord],
    sell_price: float,
    sell_qty: float,
    sell_decision_id: uuid.UUID,
) -> RealizedPnlResult | None:
    """Average-cost-basis P&L for a new sell fill, given this ticker's prior fills.

    `prior_fills` must be strictly before the new sell fill, chronologically
    ordered. The current holding period resets whenever running position
    quantity returns to zero, so cost basis never blends across separate
    buy/sell/buy round-trips — every sale within one holding period uses the
    same running average (average cost isn't consumed per-share the way FIFO
    lots would be). Returns None if there's no buy history to compute a
    basis from — a reporting gap, not something to fail on.
    """
    running_qty = 0.0
    period_buy_notional = 0.0
    period_buy_qty = 0.0
    period_decision_ids: set[uuid.UUID] = set()

    for fill in prior_fills:
        if fill.side == "buy":
            running_qty += fill.qty
            period_buy_notional += fill.price * fill.qty
            period_buy_qty += fill.qty
            period_decision_ids.add(fill.decision_id)
        else:
            running_qty -= fill.qty
            if running_qty <= 0:
                running_qty = 0.0
                period_buy_notional = 0.0
                period_buy_qty = 0.0
                period_decision_ids = set()

    if period_buy_qty <= 0:
        return None

    avg_cost_basis = period_buy_notional / period_buy_qty
    pnl_amount = (sell_price - avg_cost_basis) * sell_qty
    entry_notional = avg_cost_basis * sell_qty
    decision_ids = sorted(period_decision_ids | {sell_decision_id}, key=str)
    return RealizedPnlResult(pnl_amount=pnl_amount, decision_ids=decision_ids, entry_notional=entry_notional)
