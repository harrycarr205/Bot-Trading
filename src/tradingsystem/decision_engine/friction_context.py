"""Injects real trading-friction context into every TradingAgents prompt.

Every one of TradingAgents' 13 prompt-building nodes interpolates
`{instrument_context}`, read from `state["instrument_context"]`, which is set
exactly once per run by TradingAgentsGraph.resolve_instrument_context() (see
graph/trading_graph.py). `wrap_resolve_instrument_context` replaces that one
bound method on a graph *instance* (not the pinned package's class) so the
friction note reaches every analyst, researcher, the trader, and every risk
debator without patching the external dependency.
"""

from __future__ import annotations

from typing import Callable


def build_friction_note(
    round_trip_cost_bps: float, adv_notional: float | None, max_pct_of_adv: float
) -> str:
    """Plain-language cost/liquidity context to append to the instrument context.

    Alpaca equities are commission-free — `round_trip_cost_bps` represents an
    estimated bid-ask spread + slippage cost only, not a live spread feed.
    """
    cost_sentence = (
        f"Trading-cost note: assume an estimated round-trip transaction cost "
        f"(spread + slippage) of approximately {round_trip_cost_bps:.0f} basis "
        f"points on this position; only recommend a trade if the expected edge "
        f"clearly exceeds this cost."
    )
    if adv_notional is None or adv_notional <= 0:
        liquidity_sentence = (
            "Average daily dollar volume for this instrument is currently "
            "unavailable — treat any large position as a potential liquidity risk."
        )
    else:
        threshold = adv_notional * max_pct_of_adv
        liquidity_sentence = (
            f"Average daily dollar volume for this instrument is approximately "
            f"${adv_notional:,.0f}; a position sized above {max_pct_of_adv:.0%} of "
            f"that (${threshold:,.0f}) risks meaningful slippage and should be "
            f"flagged as an illiquidity risk in your reasoning."
        )
    return f"{cost_sentence} {liquidity_sentence}"


def wrap_resolve_instrument_context(
    original: Callable[..., str], friction_note: str
) -> Callable[..., str]:
    """Wrap a bound `resolve_instrument_context(ticker, asset_type="stock")` method
    so its return value has the friction note appended.
    """

    def wrapped(ticker: str, asset_type: str = "stock") -> str:
        return f"{original(ticker, asset_type)} {friction_note}"

    return wrapped
