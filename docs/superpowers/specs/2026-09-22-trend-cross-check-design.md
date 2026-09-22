# Independent Trend Cross-Check — Design

## Goal

Give the decision pipeline a small, numeric-only signal that is genuinely
independent of the LLM's own reasoning, and start logging whether it agrees
with each Buy/Sell-direction rating the LLM produces — without touching
sizing or order submission yet. The goal is evidence, not control: after a
few weeks of live shadow data, a real answer to "does the LLM's rating do
better when a classical trend signal agrees with it" becomes queryable.

## Problem

The Alpha Audit (published artifact, `https://claude.ai/artifact/XpEv23wjPHuT4inhTkALFh`,
§1) found that published backtests behind LLM-trading frameworks
(including TradingAgents, which this system runs) don't survive out-of-
sample replay — largely because the LLM's own training data already
contains the realized outcome for any historical ticker/date it's asked
about. Every existing signal that influences a trade in this system today
either comes from the LLM's own reasoning (the 5-tier rating) or, for
Kelly sizing, from the LLM's *own past ratings'* realized performance —
still entirely downstream of the LLM's judgment. Nothing in the pipeline
currently gives an independent read on a ticker's direction from numeric,
non-LLM data.

The audit's closing recommendation (§6): "stop treating the 5-tier rating
as your alpha source in isolation... start building a small, genuinely
walk-forward-testable statistical check... that the LLM's rating has to
agree with, or get discounted against, before it reaches position
sizing." This design builds the check and the logging; it deliberately
does **not** wire it into sizing yet (see Approaches considered).

## Approaches considered

1. **Hard gate** — disagreement blocks the trade or downgrades the
   rating outright. Closest to the audit's literal wording, but this
   system is ~3-4 weeks into a paper-trading-only observation period with
   no live-capital decision made yet (ARCHITECTURE.md §5); adding a second
   untested mechanism that can veto trades compounds the thing that
   observation period exists to de-risk. Rejected for now — a plausible
   follow-up once shadow data justifies it.
2. **Sizing discount** — disagreement scales the position size down, same
   mechanism shape as Kelly's fraction. Rejected for the same reason as
   above: no evidence yet that this specific signal, at this specific
   window, is worth acting on for *this* system's tickers and cadence.
3. **Shadow-log only (chosen)** — compute the signal, classify agreement,
   persist it on the `Decision` row, act on nothing. Matches the pattern
   already established for both other in-flight experiments (debate-rounds
   depth, Kelly sizing): ship the measurement first, decide whether to act
   on it later from real data, documented as a dated, reviewable
   experiment in ARCHITECTURE.md.

Signal choice — reusing the existing 21-day momentum/relative-volume
signal from `orchestration/ticker_selection.py` was considered and
rejected: that signal already influences *which* tickers get selected for
discovery each cycle, so using it again as the "independent" check would
partly be checking the LLM's rating against a signal that helped choose
the ticker in the first place. A dual moving-average crossover (9-day vs
50-day close, confirmed with the user) is a different window, a different
computation, and — per the audit's own §6 — the specific family of
classical trend-following signal with the most real, live, walk-forward-
validated track record of anything considered in this space.

## Design

### `risk/trend_check.py` (new)

Pure functions, no I/O — same style as `risk/kelly_sizing.py` and
`risk/stop_loss.py`, fully unit-testable with plain lists:

```python
def compute_moving_average(closes: list[float], period: int) -> float | None:
    """Simple moving average of the last `period` closes. None if fewer
    than `period` closes are available."""

def classify_trend(short_ma: float, long_ma: float, deadband_pct: float = 0.003) -> str:
    """Compares (short_ma - long_ma) / long_ma against +/- deadband_pct
    (long_ma as the base, since it's the more stable of the two lines):
    "bullish" above +deadband_pct, "bearish" below -deadband_pct,
    otherwise "neutral" (the two lines are close enough that a crossover
    call would be noise, not signal)."""

def trend_agrees_with_rating(rating: str, trend: str) -> bool | None:
    """True if the trend direction matches the rating's implied direction
    (bullish + Buy/Overweight, bearish + Sell/Underweight), False if it
    contradicts, None if trend is "neutral" (no directional call to agree
    or disagree with) or rating is "Hold" (not a directional call either —
    in practice this function is only called for non-Hold ratings, since
    cycle.py already skips Hold decisions before sizing)."""
```

### Data: reusing the existing bars fetch

`AlpacaClientProtocol.get_recent_daily_bars([ticker], lookback_days=50)`
(already used by discovery ranking and the ADV liquidity check) returns up
to 50 oldest-to-newest closes. That single fetch covers both moving
averages — the 50-day MA uses the full window, the 9-day MA uses the last
9 closes of the same window. No new data source, no new API dependency.

### Integration point: `orchestration/cycle.py`

In the per-ticker loop, immediately after the existing early-exit
(`if not result.ok or result.decision == "hold": continue`) and before the
Kelly-fraction block — this ticker already has a real Buy/Overweight/
Sell/Underweight decision at this point:

```python
trend_bars = alpaca_client.get_recent_daily_bars([ticker], lookback_days=50)
if ticker in trend_bars:
    closes = trend_bars[ticker].closes
    short_ma = compute_moving_average(closes, settings.trend_check_short_ma_days)
    long_ma = compute_moving_average(closes, settings.trend_check_long_ma_days)
    if short_ma is not None and long_ma is not None:
        trend = classify_trend(short_ma, long_ma)
        agrees = trend_agrees_with_rating(result.rating, trend)
        decision = session.get(Decision, result.decision_id)
        decision.trend_signal = trend
        decision.trend_agrees = agrees
```

A ticker with fewer than 50 days of bars available (recent IPO, thin
history) simply gets no trend check that cycle — `trend_signal`/
`trend_agrees` stay `None` on that `Decision` row, same fail-open pattern
`fetch_earnings_proximity_days` already uses for missing data. This never
blocks or delays the actual trade — it only affects what gets logged
alongside it. The existing `session.commit()` at the end of the per-
ticker loop persists it along with everything else already written that
iteration (no extra commit needed).

This is a second `get_recent_daily_bars` call for the same ticker within
one iteration (the ADV check further down makes its own 21-day-lookback
call). Deliberately not shared/cached across the two call sites — same
reasoning `compute_average_daily_dollar_volume`'s docstring already gives
for not sharing discovery's cache: each risk-adjacent read is independent
and refetches, so a change to one call site's window can never silently
affect another's.

### Schema: two nullable columns on `Decision`

```python
class Decision(Base):
    ...
    # Independent trend cross-check (docs/superpowers/specs/2026-09-22-trend-cross-check-design.md).
    # Nullable: only populated for non-Hold decisions with enough bar
    # history; never imputed when missing.
    trend_signal: Mapped[str | None]  # "bullish" | "bearish" | "neutral" | None
    trend_agrees: Mapped[bool | None]
```

Same `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` approach the Kelly-sizing
plan used (Task 1, Step 6 of
`docs/superpowers/plans/2026-09-03-fractional-kelly-sizing.md`) — run once
against both `trading` and `trading_test`, since `create_all()` won't add
columns to a table that already exists.

### Settings

```python
# Independent trend cross-check (2026-09-22): shadow-logs whether a 9-day/
# 50-day moving-average crossover agrees with each Buy/Sell-direction
# rating, on the Decision row. Logging only — does not affect sizing or
# order submission. See docs/superpowers/specs/2026-09-22-trend-cross-check-design.md.
trend_check_short_ma_days: int = 9
trend_check_long_ma_days: int = 50
```

`.env`-configurable (`TREND_CHECK_SHORT_MA_DAYS` / `TREND_CHECK_LONG_MA_DAYS`),
matching every other tunable window in this system.

### ARCHITECTURE.md: dated experiment section

A new section alongside the existing debate-rounds and Kelly-sizing
write-ups, with a review checklist in the same shape as the debate-rounds
one:

1. Query `decisions` rows with `trend_agrees IS NOT NULL`, joined to
   `realized_pnl` (via `decision_ids`, same join `get_realized_returns_by_rating`
   already does) for closed round-trips.
2. Compare realized % return where `trend_agrees = True` vs.
   `trend_agrees = False`.
3. If `trend_agrees = True` trades show a real, consistent edge after a
   reasonable sample, that's the evidence needed to consider wiring this
   into sizing (a discount, per Approach 2 above) as a *separate*,
   later design — this design does not commit to that outcome.
4. If there's no consistent difference, the signal is logged evidence
   that this particular cross-check isn't adding information for this
   system's tickers/cadence — also a useful, honest result, not a
   failure to revert anything (nothing downstream depends on it).

### Testing

- Pure tests for `compute_moving_average` (exact window, insufficient
  data returns `None`), `classify_trend` (bullish/bearish/neutral,
  deadband boundary), `trend_agrees_with_rating` (all rating/trend
  combinations, including `None` for neutral and for Hold).
- `cycle.py`-level test: a scripted Buy decision with bars where the 9dma
  clearly exceeds the 50dma asserts `trend_signal == "bullish"` and
  `trend_agrees is True` on the persisted `Decision` row; a case with
  fewer than 50 bars available asserts both fields stay `None` and the
  trade still proceeds normally (never blocks or alters the order).

## Out of scope for this design

- Any effect on sizing, rating, or order submission — shadow logging
  only, per Approach 3.
- Configurable deadband — fixed at 0.3% for now (YAGNI; revisit if the
  review shows the neutral band is miscalibrated).
- Any signal beyond a moving-average crossover (e.g. RSI, volatility
  regime) — out of scope; a later, separate signal can be added the same
  way this one was, without changing this design's shape.
- The review itself (querying `decisions`/`realized_pnl` and deciding
  whether to act on the result) — that happens after a real observation
  period, per the ARCHITECTURE.md checklist above, not as part of this
  implementation.
