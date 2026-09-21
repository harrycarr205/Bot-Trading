"""Convert a TradingAgents 5-tier rating into a concrete OrderProposal.

Deterministic, no I/O — same style as risk/validation.py. Produces a proposal that
feeds unchanged into validate_order(); this module does not itself check limits.

Long-only, no shorting: Underweight/Sell only ever reduce or exit an existing
position, never open a short. Overweight/Underweight target half the size of
Buy/Sell (confirmed with the user).

Fractional-Kelly sizing (idea #02): kelly_target_fraction, when supplied by
the caller (db/repositories.py's get_realized_returns_by_rating feeding
risk/kelly_sizing.py's compute_half_kelly_target_fraction), replaces the flat
_TARGET_FRACTION_OF_MAX multiplier for Buy/Overweight entries only — but only
when kelly_shadow_mode is False. In shadow mode (the default), the flat
fraction is still used for the actual order; the Kelly-derived alternative is
only logged, for comparison, until there's enough shadow data to trust a
cutover. See docs/superpowers/plans/2026-09-03-fractional-kelly-sizing.md.
"""

from __future__ import annotations

import datetime
import logging
import math

from tradingsystem.risk.validation import OrderProposal, PortfolioState

log = logging.getLogger(__name__)

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
    kelly_target_fraction: float | None = None,
    kelly_shadow_mode: bool = True,
) -> OrderProposal | None:
    if rating == "Hold" or current_price <= 0:
        return None

    existing_notional = portfolio.position_value_by_ticker.get(ticker, 0.0)

    if rating in ("Buy", "Overweight"):
        flat_fraction = _TARGET_FRACTION_OF_MAX[rating]
        if kelly_target_fraction is not None:
            if kelly_shadow_mode:
                log.info(
                    "Kelly shadow sizing for %s %s: flat_fraction=%.4f kelly_fraction=%.4f "
                    "(using flat_fraction — shadow mode)",
                    ticker, rating, flat_fraction, kelly_target_fraction,
                )
            else:
                log.info(
                    "Kelly sizing live for %s %s: using kelly_fraction=%.4f (flat_fraction would have been %.4f)",
                    ticker, rating, kelly_target_fraction, flat_fraction,
                )
        target_fraction = (
            flat_fraction if kelly_shadow_mode or kelly_target_fraction is None else kelly_target_fraction
        )
        target_notional = target_fraction * max_position_pct * portfolio.equity
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
    # Kelly does not apply here — it answers "how much to buy," not "how much to trim."
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
