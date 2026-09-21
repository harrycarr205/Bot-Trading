"""Deterministic per-cycle ticker selection — ARCHITECTURE.md §2/§4.

Held positions are always reassessed, uncapped; a discovery screen fills
a configurable number of additional slots from a wider candidate universe.
See docs/superpowers/specs/2026-08-25-ticker-selection-design.md.

Pure ranking/selection functions, no I/O — same style as risk/stop_loss.py
and risk/circuit_breaker.py.
"""

from __future__ import annotations

import datetime
import logging

import yfinance as yf

from tradingsystem.execution.alpaca_client import AlpacaClientProtocol, DailyBars

log = logging.getLogger(__name__)


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


def compute_earnings_proximity_score(days_until: int | None, horizon_days: int = 10) -> float:
    """Lower score = more worth watching this cycle (closer to earnings).

    None (no data), negative (a stale/unrefreshed past date), or beyond
    horizon_days all collapse to the same neutral baseline — earnings
    proximity is only a meaningful signal within a near-term window, and a
    missing-data ticker should rank the same as a genuinely-distant one, not
    be penalized or favored by an artifact of missing data.
    """
    if days_until is None or days_until < 0 or days_until > horizon_days:
        return float(horizon_days)
    return float(days_until)


def rank_candidates(
    bars_by_ticker: dict[str, DailyBars],
    earnings_days_by_ticker: dict[str, int | None] | None = None,
) -> list[str]:
    """Best-to-worst by composite rank: momentum rank + relative-volume rank
    (+ earnings-proximity rank, when earnings_days_by_ticker is supplied),
    all computed cross-sectionally across the tickers present this cycle.
    Ties broken alphabetically for determinism.

    earnings_days_by_ticker is optional and purely additive: omitting it (or
    passing an empty dict) reproduces the original 2-factor ranking exactly.
    It is not folded in as an always-present third dimension, because an
    all-neutral score would still assign 0..n-1 ranks by alphabetical
    tiebreak and perturb the composite even with zero real earnings signal.
    """
    momentum = {t: compute_momentum_pct(b.closes) for t, b in bars_by_ticker.items()}
    rel_vol = {t: compute_relative_volume(b.volumes) for t, b in bars_by_ticker.items()}

    momentum_rank = _dense_rank(momentum, descending=True)
    volume_rank = _dense_rank(rel_vol, descending=True)

    composite = {t: momentum_rank[t] + volume_rank[t] for t in bars_by_ticker}

    if earnings_days_by_ticker:
        earnings_score = {
            t: compute_earnings_proximity_score(earnings_days_by_ticker.get(t))
            for t in bars_by_ticker
        }
        earnings_rank = _dense_rank(earnings_score, descending=False)
        composite = {t: composite[t] + earnings_rank[t] for t in bars_by_ticker}

    return sorted(composite, key=lambda t: (composite[t], t))


def _dense_rank(scores: dict[str, float], descending: bool) -> dict[str, int]:
    """Same rank for equal scores (0-indexed) rather than each tied ticker
    claiming a distinct sequential slot. A genuine tie must carry zero
    differential into the composite score — alphabetical order is only the
    final tiebreak applied to the composite sort (see rank_candidates), not
    an accidental per-factor bias every tied pair would otherwise pick up.
    """
    unique_values = sorted(set(scores.values()), reverse=descending)
    value_to_rank = {value: i for i, value in enumerate(unique_values)}
    return {ticker: value_to_rank[value] for ticker, value in scores.items()}


def select_tickers_for_cycle(
    held_tickers: set[str], ranked_candidates: list[str], discovery_slots: int
) -> list[str]:
    """held_tickers (always included, uncapped) plus the top `discovery_slots`
    ranked candidates not already held.
    """
    discovery = [t for t in ranked_candidates if t not in held_tickers][:discovery_slots]
    result = sorted(held_tickers | set(discovery))
    log.info("cycle ticker list: held=%s discovery=%s", sorted(held_tickers), discovery)
    if not result:
        log.warning("cycle ticker list is empty: nothing held and nothing discovered")
    return result


def build_cycle_ticker_list(
    alpaca_client: AlpacaClientProtocol,
    candidate_universe: list[str],
    discovery_slots: int,
    lookback_days: int = 21,
) -> list[str]:
    held = {p.ticker for p in alpaca_client.get_position_details()}
    bars = alpaca_client.get_recent_daily_bars(candidate_universe, lookback_days)
    earnings_days = fetch_earnings_proximity_days(candidate_universe, datetime.date.today())
    ranked = rank_candidates(bars, earnings_days)
    return select_tickers_for_cycle(held, ranked, discovery_slots)


def fetch_earnings_proximity_days(
    tickers: list[str], as_of: datetime.date
) -> dict[str, int | None]:
    """Best-effort days-until-next-earnings per ticker.

    Fail-open per ticker, same pattern as TradingAgents' own
    resolve_instrument_identity: a yfinance lookup failure, rate limit, or a
    ticker with no calendar data returns None for that ticker rather than
    raising, so one bad lookup never blocks the whole discovery screen.
    """
    result: dict[str, int | None] = {}
    for ticker in tickers:
        try:
            calendar = yf.Ticker(ticker).calendar or {}
            future_dates = [d for d in calendar.get("Earnings Date") or [] if d >= as_of]
            result[ticker] = (min(future_dates) - as_of).days if future_dates else None
        except Exception as exc:  # noqa: BLE001 - yfinance failures aren't consistently typed
            log.warning("earnings-date lookup failed for %s: %s", ticker, exc)
            result[ticker] = None
    return result
