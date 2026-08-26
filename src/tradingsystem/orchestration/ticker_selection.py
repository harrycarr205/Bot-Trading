"""Deterministic per-cycle ticker selection — ARCHITECTURE.md §2/§4.

Held positions are always reassessed, uncapped; a discovery screen fills
a configurable number of additional slots from a wider candidate universe.
See docs/superpowers/specs/2026-08-25-ticker-selection-design.md.

Pure ranking/selection functions, no I/O — same style as risk/stop_loss.py
and risk/circuit_breaker.py.
"""

from __future__ import annotations

from tradingsystem.execution.alpaca_client import AlpacaClientProtocol, DailyBars


def compute_momentum_pct(closes: list[float]) -> float:
    """% change from the oldest to the newest close in the window."""
    if len(closes) < 2 or closes[0] <= 0:
        return 0.0
    return (closes[-1] - closes[0]) / closes[0]


def compute_relative_volume(volumes: list[float]) -> float:
    """Most recent day's volume / average of the preceding days. 1.0 = neutral."""
    if len(volumes) < 2:
        return 1.0
    baseline = volumes[:-1]
    avg_baseline = sum(baseline) / len(baseline)
    if avg_baseline <= 0:
        return 1.0
    return volumes[-1] / avg_baseline


def rank_candidates(bars_by_ticker: dict[str, DailyBars]) -> list[str]:
    """Best-to-worst by composite rank: momentum rank + relative-volume rank,
    both computed cross-sectionally across the tickers present this cycle.
    Ties broken alphabetically for determinism.
    """
    momentum = {t: compute_momentum_pct(b.closes) for t, b in bars_by_ticker.items()}
    rel_vol = {t: compute_relative_volume(b.volumes) for t, b in bars_by_ticker.items()}

    momentum_order = sorted(momentum, key=lambda t: (-momentum[t], t))
    volume_order = sorted(rel_vol, key=lambda t: (-rel_vol[t], t))
    momentum_rank = {t: i for i, t in enumerate(momentum_order)}
    volume_rank = {t: i for i, t in enumerate(volume_order)}

    composite = {t: momentum_rank[t] + volume_rank[t] for t in bars_by_ticker}
    return sorted(composite, key=lambda t: (composite[t], t))


def select_tickers_for_cycle(
    held_tickers: set[str], ranked_candidates: list[str], discovery_slots: int
) -> list[str]:
    """held_tickers (always included, uncapped) plus the top `discovery_slots`
    ranked candidates not already held.
    """
    discovery = [t for t in ranked_candidates if t not in held_tickers][:discovery_slots]
    return sorted(held_tickers | set(discovery))


def build_cycle_ticker_list(
    alpaca_client: AlpacaClientProtocol,
    candidate_universe: list[str],
    discovery_slots: int,
    lookback_days: int = 21,
) -> list[str]:
    held = {p.ticker for p in alpaca_client.get_position_details()}
    bars = alpaca_client.get_recent_daily_bars(candidate_universe, lookback_days)
    ranked = rank_candidates(bars)
    return select_tickers_for_cycle(held, ranked, discovery_slots)
