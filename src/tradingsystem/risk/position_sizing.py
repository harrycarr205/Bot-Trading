"""Convert a TradingAgents 5-tier rating into a concrete OrderProposal.

Deterministic, no I/O — same style as risk/validation.py. Produces a proposal that
feeds unchanged into validate_order(); this module does not itself check limits.

Long-only, no shorting: Underweight/Sell only ever reduce or exit an existing
position, never open a short. Overweight/Underweight target half the size of
Buy/Sell (confirmed with the user).
"""

from __future__ import annotations

import datetime
import math

from tradingsystem.risk.validation import OrderProposal, PortfolioState

_TARGET_FRACTION_OF_MAX = {
    "Buy": 1.0,
    "Overweight": 0.5,
    "Underweight": 0.5,  # target: reduce TO this fraction of current position
    "Sell": 0.0,
}


def size_order(
    rating: str,
    ticker: str,
    portfolio: PortfolioState,
    current_price: float,
    max_position_pct: float,
    data_timestamp: datetime.datetime,
) -> OrderProposal | None:
    if rating == "Hold" or current_price <= 0:
        return None

    existing_notional = portfolio.position_value_by_ticker.get(ticker, 0.0)

    if rating in ("Buy", "Overweight"):
        target_notional = _TARGET_FRACTION_OF_MAX[rating] * max_position_pct * portfolio.equity
        need_notional = target_notional - existing_notional
        if need_notional <= 0:
            return None
        qty = math.floor(need_notional / current_price)
        if qty < 1:
            return None
        return OrderProposal(
            ticker=ticker,
            side="buy",
            order_type="limit",
            qty=qty,
            limit_price=current_price,
            data_timestamp=data_timestamp,
        )

    # Underweight / Sell: only ever reduce an existing position, never short.
    if existing_notional <= 0:
        return None
    target_notional = _TARGET_FRACTION_OF_MAX[rating] * existing_notional
    reduce_by_notional = existing_notional - target_notional
    if reduce_by_notional <= 0:
        return None
    qty = math.floor(reduce_by_notional / current_price)
    if qty < 1:
        return None
    return OrderProposal(
        ticker=ticker,
        side="sell",
        order_type="limit",
        qty=qty,
        limit_price=current_price,
        data_timestamp=data_timestamp,
    )
