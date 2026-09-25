# Independent Trend Cross-Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shadow-log whether a 9-day/50-day moving-average crossover agrees with each non-Hold decision's rating, giving the pipeline its first signal that is genuinely independent of the LLM's own reasoning — without touching sizing or order submission.

**Architecture:** Three layers, bottom-up: a new pure module (`risk/trend_check.py`) computes moving averages and classifies agreement; two nullable columns on the existing `Decision` model persist the result; `orchestration/cycle.py` computes and attaches it to each decision using a bars fetch already available on `AlpacaClientProtocol`. Nothing downstream (sizing, order submission) reads these fields — this plan only ships the measurement.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0, pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-trend-cross-check-design.md`. This plan is self-contained; the spec is provenance and rationale (including the Alpha Audit citation and rejected approaches).

## Global Constraints

- **This plan only ships shadow-mode logging.** No task changes `risk/position_sizing.py`, order sizing, or order submission. Acting on the signal (a gate or a sizing discount) is an explicit, separate, later decision per the spec's Approaches 1/2 — not part of this plan.
- The trend check must **never block or delay a trade**. A ticker with fewer than `trend_check_long_ma_days` bars of history simply leaves `trend_signal`/`trend_agrees` as `None` on that `Decision` row — it never raises, never skips the ticker, never affects `size_order`.
- `Base.metadata.create_all()` (`db/init_db.py`) only creates missing *tables*, not missing *columns* on tables that already exist. Both `trading` (live) and `trading_test` already have a `decisions` table, so Task 2 includes an explicit, idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` step against both databases, same approach the fractional-Kelly-sizing plan used (`docs/superpowers/plans/2026-09-03-fractional-kelly-sizing.md`, Task 1 Step 6).
- The deadband (0.3%) and the two moving-average windows (9/50 days) are the only tunables — no other signal (RSI, volatility) is in scope.

---

### Task 1: Trend-check math (pure)

**Files:**
- Create: `src/tradingsystem/risk/trend_check.py`
- Test: `tests/test_trend_check.py`

**Interfaces:**
- Produces: `compute_moving_average(closes: list[float], period: int) -> float | None`; `classify_trend(short_ma: float, long_ma: float, deadband_pct: float = 0.003) -> str` (returns `"bullish"` / `"bearish"` / `"neutral"`); `trend_agrees_with_rating(rating: str, trend: str) -> bool | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trend_check.py`:

```python
from tradingsystem.risk.trend_check import (
    classify_trend,
    compute_moving_average,
    trend_agrees_with_rating,
)


def test_compute_moving_average_exact_window():
    assert compute_moving_average([10.0, 20.0, 30.0], period=3) == 20.0


def test_compute_moving_average_uses_only_the_last_period_closes():
    # Last 3 of [1,2,3,4,5] are [3,4,5] -> avg 4.0; the older 1,2 are ignored.
    assert compute_moving_average([1.0, 2.0, 3.0, 4.0, 5.0], period=3) == 4.0


def test_compute_moving_average_insufficient_data_returns_none():
    assert compute_moving_average([1.0, 2.0], period=5) is None


def test_classify_trend_bullish_above_deadband():
    # (110 - 100) / 100 = 0.10, well above the 0.003 default deadband.
    assert classify_trend(short_ma=110.0, long_ma=100.0) == "bullish"


def test_classify_trend_bearish_below_deadband():
    assert classify_trend(short_ma=90.0, long_ma=100.0) == "bearish"


def test_classify_trend_neutral_within_deadband():
    # (100.2 - 100) / 100 = 0.002, inside the 0.003 deadband.
    assert classify_trend(short_ma=100.2, long_ma=100.0) == "neutral"


def test_classify_trend_deadband_boundary_is_neutral():
    # Exactly at the deadband threshold (0.3%) counts as neutral, not
    # bullish -- the comparison is strictly greater-than, not >=.
    assert classify_trend(short_ma=100.3, long_ma=100.0) == "neutral"


def test_trend_agrees_with_rating_bullish_matches_buy():
    assert trend_agrees_with_rating("Buy", "bullish") is True


def test_trend_agrees_with_rating_bullish_matches_overweight():
    assert trend_agrees_with_rating("Overweight", "bullish") is True


def test_trend_agrees_with_rating_bearish_contradicts_buy():
    assert trend_agrees_with_rating("Buy", "bearish") is False


def test_trend_agrees_with_rating_bearish_matches_sell():
    assert trend_agrees_with_rating("Sell", "bearish") is True


def test_trend_agrees_with_rating_bearish_matches_underweight():
    assert trend_agrees_with_rating("Underweight", "bearish") is True


def test_trend_agrees_with_rating_bullish_contradicts_sell():
    assert trend_agrees_with_rating("Sell", "bullish") is False


def test_trend_agrees_with_rating_neutral_is_none_regardless_of_rating():
    assert trend_agrees_with_rating("Buy", "neutral") is None
    assert trend_agrees_with_rating("Sell", "neutral") is None


def test_trend_agrees_with_rating_hold_is_none():
    # Hold makes no directional claim -- never actually called with "Hold"
    # in practice (cycle.py skips Hold decisions before this runs), but
    # the function must not raise or guess if it ever is.
    assert trend_agrees_with_rating("Hold", "bullish") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_trend_check.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradingsystem.risk.trend_check'`.

- [ ] **Step 3: Implement `risk/trend_check.py`**

Create `src/tradingsystem/risk/trend_check.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_trend_check.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/risk/trend_check.py tests/test_trend_check.py
git commit -m "feat: add trend cross-check moving-average math"
```

---

### Task 2: Schema, settings, and wiring into `cycle.py`

**Files:**
- Modify: `src/tradingsystem/db/models.py` (`Decision` class)
- Modify: `src/tradingsystem/config.py`
- Modify: `src/tradingsystem/orchestration/cycle.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`, `tests/test_cycle.py`

**Interfaces:**
- Consumes: `compute_moving_average`, `classify_trend`, `trend_agrees_with_rating` from Task 1.
- Produces: `Decision.trend_signal: str | None`, `Decision.trend_agrees: bool | None`; `Settings().trend_check_short_ma_days: int` (default `9`); `Settings().trend_check_long_ma_days: int` (default `50`).

- [ ] **Step 1: Write the failing `Settings` tests**

Add to `tests/test_config.py`:

```python
def test_trend_check_short_ma_days_default():
    assert Settings(_env_file=None).trend_check_short_ma_days == 9


def test_trend_check_long_ma_days_default():
    assert Settings(_env_file=None).trend_check_long_ma_days == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Add the two `Settings` fields**

In `src/tradingsystem/config.py`, add after `kelly_min_sample_size` (before `kill_switch_file`):

```python
    # Independent trend cross-check (2026-09-22): shadow-logs whether a
    # short/long moving-average crossover agrees with each Buy/Sell-
    # direction decision's rating, on the Decision row. Logging only --
    # does not affect sizing or order submission. See
    # docs/superpowers/specs/2026-09-22-trend-cross-check-design.md.
    trend_check_short_ma_days: int = 9
    trend_check_long_ma_days: int = 50
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Add the two columns to `Decision`**

In `src/tradingsystem/db/models.py`, add to the `Decision` class, after `reasoning_summary` and before `created_at`:

```python
    # Independent trend cross-check (docs/superpowers/specs/2026-09-22-trend-cross-check-design.md).
    # Nullable: only populated for non-Hold decisions with enough bar
    # history; never imputed when missing.
    trend_signal: Mapped[str | None]  # "bullish" | "bearish" | "neutral" | None
    trend_agrees: Mapped[bool | None]
```

- [ ] **Step 6: Apply the columns to both live databases**

Run once against each database (idempotent -- safe to re-run):

```bash
.venv/Scripts/python.exe -c "
from sqlalchemy import create_engine, text
from tradingsystem.config import Settings

settings = Settings()
for url, name in [(settings.database_url, 'trading'), (settings.test_database_url, 'trading_test')]:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE decisions ADD COLUMN IF NOT EXISTS trend_signal VARCHAR'))
        conn.execute(text('ALTER TABLE decisions ADD COLUMN IF NOT EXISTS trend_agrees BOOLEAN'))
    engine.dispose()
    print(f'{name}: trend_signal/trend_agrees columns present')
"
```

Expected output: `trading: trend_signal/trend_agrees columns present` and `trading_test: trend_signal/trend_agrees columns present`.

- [ ] **Step 7: Write the failing `cycle.py`-level tests**

Add to `tests/test_cycle.py`:

```python
def test_buy_decision_persists_trend_cross_check(db_session, monkeypatch):
    ScriptedGraph.calls = [(make_final_state("Buy: strong fundamentals"), "Buy")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)
    from tradingsystem.execution.alpaca_client import DailyBars, SubmittedOrder

    # 41 closes at 100.0 then 9 at 110.0: 50-day MA = 101.8, 9-day MA = 110.0
    # -> 9dma clearly above 50dma -> "bullish", which agrees with a Buy rating.
    closes = [100.0] * 41 + [110.0] * 9
    client = FakeAlpacaClient(
        market_status="open", equity=100_000.0, price=100.0,
        submit_response=SubmittedOrder(alpaca_order_id="trend1", status="new"),
        bars={"AAPL": DailyBars(ticker="AAPL", closes=closes, volumes=[1_000_000.0] * 50)},
    )

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=["AAPL"])

    decision = db_session.query(Decision).filter_by(decision="buy").one()
    assert decision.trend_signal == "bullish"
    assert decision.trend_agrees is True


def test_buy_decision_with_insufficient_bar_history_leaves_trend_fields_none(db_session, monkeypatch):
    ScriptedGraph.calls = [(make_final_state("Buy: strong fundamentals"), "Buy")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)
    from tradingsystem.execution.alpaca_client import DailyBars, SubmittedOrder

    client = FakeAlpacaClient(
        market_status="open", equity=100_000.0, price=100.0,
        submit_response=SubmittedOrder(alpaca_order_id="trend2", status="new"),
        bars={"AAPL": DailyBars(ticker="AAPL", closes=[100.0], volumes=[1_000_000.0])},
    )

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=["AAPL"])

    decision = db_session.query(Decision).filter_by(decision="buy").one()
    assert decision.trend_signal is None
    assert decision.trend_agrees is None
    # The missing trend data never blocks or alters the trade itself.
    assert client.submit_calls == [("AAPL", "buy", 100, 100.0)]
```

- [ ] **Step 8: Run tests to verify they fail**

Run: `pytest tests/test_cycle.py -v`
Expected: FAIL -- `Decision` has no `trend_signal`/`trend_agrees` attribute yet, and `cycle.py` doesn't set them.

- [ ] **Step 9: Wire the trend check into `cycle.py`**

In `src/tradingsystem/orchestration/cycle.py`, update the import block (currently ends with the five `risk.*` imports at lines 22-26):

```python
from tradingsystem.risk.circuit_breaker import check_daily_breaker, check_weekly_breaker
from tradingsystem.risk.kelly_sizing import compute_half_kelly_target_fraction
from tradingsystem.risk.position_sizing import size_order
from tradingsystem.risk.stop_loss import is_stop_loss_triggered
from tradingsystem.risk.trend_check import classify_trend, compute_moving_average, trend_agrees_with_rating
from tradingsystem.risk.validation import OrderProposal
```

In the per-ticker loop, immediately after the existing early-exit (currently):

```python
            if not result.ok or result.decision == "hold":
                continue

            portfolio = build_portfolio_state(alpaca_client)
```

insert the trend-check block between them:

```python
            if not result.ok or result.decision == "hold":
                continue

            trend_bars = alpaca_client.get_recent_daily_bars(
                [ticker], lookback_days=settings.trend_check_long_ma_days
            )
            if ticker in trend_bars:
                closes = trend_bars[ticker].closes
                short_ma = compute_moving_average(closes, settings.trend_check_short_ma_days)
                long_ma = compute_moving_average(closes, settings.trend_check_long_ma_days)
                if short_ma is not None and long_ma is not None:
                    trend = classify_trend(short_ma, long_ma)
                    decision_row = session.get(Decision, result.decision_id)
                    decision_row.trend_signal = trend
                    decision_row.trend_agrees = trend_agrees_with_rating(result.rating, trend)

            portfolio = build_portfolio_state(alpaca_client)
```

This runs before the existing `kelly_target_fraction`/`size_order` block, and never returns early or raises -- a ticker missing from `trend_bars`, or with too few closes for either moving average, simply skips the block and the loop continues into sizing exactly as before. The existing `session.commit()` at the end of the per-ticker loop persists `decision_row`'s changes along with everything else already written that iteration.

- [ ] **Step 10: Run tests to verify they pass**

Run: `pytest tests/test_cycle.py -v`
Expected: PASS -- the two new tests, plus every pre-existing test in this file (each supplies at most 1 close per ticker in `bars`, so `compute_moving_average` returns `None` for both windows and the trend fields stay unset, identical to today's behavior).

- [ ] **Step 11: Update `.env.example`**

In `.env.example`, add after the existing `KELLY_MIN_SAMPLE_SIZE=10` line (before the `# --- Discord alerting ---` section):

```
# --- Independent trend cross-check (2026-09-22) -- shadow-logs whether a
# short/long moving-average crossover agrees with each Buy/Sell-direction
# rating, on the Decision row. Logging only; does not affect sizing or
# order submission. ---
TREND_CHECK_SHORT_MA_DAYS=9
TREND_CHECK_LONG_MA_DAYS=50
```

- [ ] **Step 12: Run the full test suite to check for regressions**

Run: `pytest -v`
Expected: no new failures.

- [ ] **Step 13: Commit**

```bash
git add src/tradingsystem/db/models.py src/tradingsystem/config.py src/tradingsystem/orchestration/cycle.py .env.example tests/test_config.py tests/test_cycle.py
git commit -m "feat: shadow-log an independent moving-average trend check per decision"
```

---

### Task 3: ARCHITECTURE.md review checklist (documentation, not code)

**Files:**
- Modify: `ARCHITECTURE.md` -- add a dated section immediately after the existing "Tool-usage audit finding (2026-09-03)" section (search for that heading; the new section goes directly above the `---` that follows it, before "## 6. Scheduling & runtime architecture").

- [ ] **Step 1: Add the review checklist to `ARCHITECTURE.md`**

Insert this section after "Tool-usage audit finding (2026-09-03)"'s closing paragraph and before the following `---`:

```markdown
### Trend cross-check experiment (2026-09-22)

A dual moving-average crossover (`trend_check_short_ma_days` /
`trend_check_long_ma_days`, default 9/50) now shadow-logs onto every
non-Hold `Decision` row: `trend_signal` ("bullish"/"bearish"/"neutral")
and `trend_agrees` (whether the crossover direction matches the LLM
rating's implied direction). This does not affect sizing or order
submission -- see
`docs/superpowers/specs/2026-09-22-trend-cross-check-design.md` for the
full design and rationale (built in response to the Alpha Audit's
closing recommendation, published artifact
`https://claude.ai/artifact/XpEv23wjPHuT4inhTkALFh`).

Review after a few weeks of live cycles:

1. Query `decisions` rows with `trend_agrees IS NOT NULL`, joined to
   `realized_pnl` for closed round-trips (same `decision_ids` join
   `get_realized_returns_by_rating` in `db/repositories.py` already uses).
2. Compare realized % return (`pnl_amount / entry_notional`) where
   `trend_agrees = True` vs. `trend_agrees = False`.
3. If `trend_agrees = True` trades show a real, consistent edge after a
   reasonable sample, that's the evidence needed to consider a follow-up
   design that discounts position size on disagreement -- not something
   this experiment does automatically.
4. If there's no consistent difference, that's still a useful result:
   this particular cross-check isn't adding information for this
   system's tickers/cadence. Nothing downstream depends on it, so there
   is nothing to revert either way.
```

- [ ] **Step 2: Commit**

```bash
git add ARCHITECTURE.md
git commit -m "docs: add review checklist for the trend cross-check experiment"
```

---

## Self-Review

**1. Spec coverage:** The design's three approaches map directly: Approach 3 (shadow-log only) is exactly what Task 2 wires up; Approaches 1/2 (gate, discount) are explicitly not implemented -- no task touches `position_sizing.py`. The signal-choice rationale (9/50-day crossover, not the existing 21-day momentum signal) is realized in Task 1's module and docstring. The schema, settings, integration point, and testing sections of the spec each map to a step in Task 2. The ARCHITECTURE.md review checklist from the spec is Task 3, verbatim.

**2. Placeholder scan:** No TBDs. The ALTER TABLE step (Task 2, Step 6) and the ARCHITECTURE.md block (Task 3, Step 1) are literal, runnable instructions, not stubs.

**3. Type consistency:** `compute_moving_average(closes: list[float], period: int) -> float | None` (Task 1) is called identically in Task 2's `cycle.py` wiring with the same parameter order. `classify_trend(short_ma: float, long_ma: float, deadband_pct: float = 0.003) -> str` (Task 1) is called in Task 2 without overriding `deadband_pct`, matching the spec's fixed-default decision. `trend_agrees_with_rating(rating: str, trend: str) -> bool | None` (Task 1) is called in Task 2 with `result.rating` (a `str`, produced by `decision_engine/runner.py`'s existing `ResearchResult`) and `trend` (Task 1's own `classify_trend` output) -- types line up end to end. `Decision.trend_signal: str | None` / `Decision.trend_agrees: bool | None` (Task 2, Step 5) match exactly what Task 2's `cycle.py` wiring assigns.
