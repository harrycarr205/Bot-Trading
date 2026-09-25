# Fractional-Kelly Position Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat 100%/50% position-size split with a Half-Kelly fraction derived from this system's own realized-trade history, run in **shadow mode first** (logged alongside the existing flat sizing, not acted on) so there's real before/after data before switching over.

**Architecture:** Four layers, bottom-up:

1. **Data**: `execution/realized_pnl.py`'s `compute_realized_pnl` already computes `avg_cost_basis` internally but discards it — `RealizedPnlResult` gains an `entry_notional` field (`avg_cost_basis * sell_qty`), persisted to a new nullable `RealizedPnl.entry_notional` column. This is the one piece of data needed to turn a dollar `pnl_amount` into a % return, and it doesn't exist anywhere in the schema today.
2. **Math**: a new pure module, `risk/kelly_sizing.py`, computes the standard Kelly fraction `f* = (p*b - q) / b` from a win rate and payoff ratio, then Half-Kelly, clamped to `[0, 1]` — no I/O, no DB access, same style as `risk/stop_loss.py`.
3. **Retrieval**: `db/repositories.py` gains `get_realized_returns_by_rating`, which joins `RealizedPnl` back to `Decision` via the existing `decision_ids` array column to bucket historical % returns by entry rating (Buy/Overweight).
4. **Sizing**: `risk/position_sizing.py:size_order` gains two new optional parameters — `kelly_target_fraction` (precomputed by the caller) and `kelly_shadow_mode` (default `True`). When shadow mode is on (the default, and the only mode this plan turns on), the function's actual output is **byte-for-byte identical to today** — every existing test in `tests/test_position_sizing.py` passes unmodified — but it logs what Kelly would have sized instead, for comparison. `orchestration/cycle.py` computes the realized-returns buckets once per cycle and threads the per-decision Kelly fraction through.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0, pytest. No live-DB migration tool exists in this project (`db/init_db.py` uses `Base.metadata.create_all()`, which does not alter existing tables) — Task 1 includes an explicit, idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` step against both the real `trading` database and the `trading_test` database, since `create_all()` will not add the new column to either on its own once the table already exists.

**Spec:** The Alpha Backlog (idea #02, "Replace the flat 100/50 size split with fractional-Kelly sizing"), published artifact `https://claude.ai/code/artifact/58853951-b14c-4bfd-9d7e-97d14c43b033`. This plan document is self-contained; the artifact is provenance only.

## Global Constraints

- **This plan only ships shadow mode.** `Settings.kelly_sizing_shadow_mode` defaults to `True` and nothing in this plan flips it to `False`. The backlog is explicit that this should be "shadow-run before switching over" — cutover is a deliberate, separate, later decision made once there's enough logged shadow data to compare against, not something this plan does automatically.
- Kelly sizing only ever applies to entries (`Buy`/`Overweight` ratings). `Underweight`/`Sell` exit sizing in `position_sizing.py` is completely untouched — Kelly answers "how much to buy," not "how much to trim."
- The existing 10%-of-equity ceiling (`max_position_pct`, `risk/validation.py:check_position_size`) stays the hard ceiling either way, per the backlog's explicit instruction: "Keep the existing 10%-of-equity cap as the ceiling either way — this changes what happens under the ceiling, not the ceiling itself." The Kelly fraction only ever multiplies `max_position_pct`, exactly like `_TARGET_FRACTION_OF_MAX` does today — it is clamped to `[0, 1]` and can never exceed the existing cap.
- `RealizedPnl.decision_ids` is stored sorted by UUID string (`execution/realized_pnl.py:69`), not chronologically — there is no reliable way, without a further schema change, to identify which specific decision "opened" a multi-buy holding period. `get_realized_returns_by_rating` (Task 3) documents and accepts this: a round-trip whose `decision_ids` include both a `Buy`- and an `Overweight`-rated decision contributes its return to both buckets. This is a deliberate, stated approximation for a first version, not a bug.
- A trade this system can't yet compute an entry rating for (rows written before `entry_notional` existed, or a round-trip with no Buy/Overweight decision among its `decision_ids`) is simply excluded from the Kelly calculation — never estimated or imputed.

---

### Task 1: Persist `entry_notional` on `RealizedPnl`

**Files:**
- Modify: `src/tradingsystem/execution/realized_pnl.py`
- Modify: `src/tradingsystem/db/models.py`
- Modify: `src/tradingsystem/execution/executor.py`
- Test: `tests/test_realized_pnl.py`, `tests/test_executor.py`

**Interfaces:**
- Produces: `RealizedPnlResult.entry_notional: float` (new field). `RealizedPnl.entry_notional: float | None` (new nullable DB column).

- [ ] **Step 1: Write the failing tests for `compute_realized_pnl`**

Add to `tests/test_realized_pnl.py`:

```python
def test_single_buy_full_sell_computes_entry_notional():
    prior = [make_buy(100.0, 10)]
    result = compute_realized_pnl(prior, sell_price=110.0, sell_qty=10, sell_decision_id=SELL_DECISION)
    assert result.entry_notional == 1000.0  # avg cost basis 100.0 * 10 shares sold


def test_multiple_buys_entry_notional_uses_weighted_average_cost():
    prior = [make_buy(100.0, 10, decision_id=BUY_DECISION), make_buy(120.0, 10, decision_id=BUY_DECISION_2)]
    result = compute_realized_pnl(prior, sell_price=115.0, sell_qty=20, sell_decision_id=SELL_DECISION)
    assert result.entry_notional == 2200.0  # avg cost basis 110.0 * 20 shares sold


def test_partial_sell_entry_notional_scales_with_sell_qty_not_full_position():
    prior = [make_buy(100.0, 10)]
    result = compute_realized_pnl(prior, sell_price=110.0, sell_qty=4, sell_decision_id=SELL_DECISION)
    assert result.entry_notional == 400.0  # avg cost basis 100.0 * 4 shares sold (not all 10)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_realized_pnl.py -v`
Expected: FAIL with `AttributeError: 'RealizedPnlResult' object has no attribute 'entry_notional'`.

- [ ] **Step 3: Add `entry_notional` to `RealizedPnlResult` and `compute_realized_pnl`**

In `src/tradingsystem/execution/realized_pnl.py`, replace the `RealizedPnlResult` dataclass (lines 23-26):

```python
@dataclasses.dataclass(frozen=True)
class RealizedPnlResult:
    pnl_amount: float
    decision_ids: list[uuid.UUID]
    entry_notional: float
```

Replace the end of `compute_realized_pnl` (lines 64-70):

```python
    if period_buy_qty <= 0:
        return None

    avg_cost_basis = period_buy_notional / period_buy_qty
    pnl_amount = (sell_price - avg_cost_basis) * sell_qty
    entry_notional = avg_cost_basis * sell_qty
    decision_ids = sorted(period_decision_ids | {sell_decision_id}, key=str)
    return RealizedPnlResult(pnl_amount=pnl_amount, decision_ids=decision_ids, entry_notional=entry_notional)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_realized_pnl.py -v`
Expected: PASS (all tests, including the 6 pre-existing ones — they only assert `.pnl_amount`/`.decision_ids`, unaffected by the new field).

- [ ] **Step 5: Add the `entry_notional` column to the `RealizedPnl` model**

In `src/tradingsystem/db/models.py`, add to the `RealizedPnl` class (after `pnl_amount`, before `closed_at`):

```python
    # Cost-basis notional of the shares this row closed (avg_cost_basis *
    # sell_qty from execution/realized_pnl.py) — needed to compute a %
    # return (pnl_amount / entry_notional) for risk/kelly_sizing.py.
    # Nullable: rows written before 2026-09-03 don't have it and are simply
    # excluded from the Kelly win-rate/payoff-ratio calculation, never imputed.
    entry_notional: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
```

- [ ] **Step 6: Apply the column to both live databases**

`Base.metadata.create_all()` (`db/init_db.py`) only creates missing *tables*, not missing *columns* on tables that already exist — both `trading` (live) and `trading_test` already have a `realized_pnl` table from earlier runs. Run this once against each database (idempotent — safe to re-run):

```bash
.venv/Scripts/python.exe -c "
from sqlalchemy import create_engine, text
from tradingsystem.config import Settings

settings = Settings()
for url, name in [(settings.database_url, 'trading'), (settings.test_database_url, 'trading_test')]:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE realized_pnl ADD COLUMN IF NOT EXISTS entry_notional NUMERIC(14, 4)'))
    engine.dispose()
    print(f'{name}: entry_notional column present')
"
```

Expected output: `trading: entry_notional column present` and `trading_test: entry_notional column present`.

- [ ] **Step 7: Persist `entry_notional` in `executor.py`**

In `src/tradingsystem/execution/executor.py`, update the `RealizedPnl(...)` construction inside `sync_order_fills` (lines 156-158):

```python
            if realized is not None:
                session.add(RealizedPnl(
                    ticker=order.ticker, decision_ids=realized.decision_ids,
                    pnl_amount=realized.pnl_amount, entry_notional=realized.entry_notional,
                    closed_at=status.filled_at,
                ))
```

- [ ] **Step 8: Write the failing test for persistence**

Add to `tests/test_executor.py`, extending `test_sell_fill_computes_and_persists_realized_pnl`'s assertions (after the existing `assert realized.closed_at == sell_filled_at` line):

```python
    assert realized.entry_notional == 1000.0  # avg cost basis 100.0 * 10 shares sold
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/test_realized_pnl.py tests/test_executor.py -v`
Expected: PASS.

- [ ] **Step 10: Run the full test suite to check for regressions**

Run: `pytest -v`
Expected: no new failures.

- [ ] **Step 11: Commit**

```bash
git add src/tradingsystem/execution/realized_pnl.py src/tradingsystem/db/models.py src/tradingsystem/execution/executor.py tests/test_realized_pnl.py tests/test_executor.py
git commit -m "feat: persist entry_notional on RealizedPnl for Kelly-sizing input"
```

---

### Task 2: Kelly-fraction math (pure)

**Files:**
- Create: `src/tradingsystem/risk/kelly_sizing.py`
- Test: `tests/test_kelly_sizing.py`

**Interfaces:**
- Produces: `compute_kelly_fraction(win_rate: float, payoff_ratio: float) -> float`; `compute_half_kelly_target_fraction(returns_pct: list[float], min_sample_size: int, max_fraction: float = 1.0) -> float | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kelly_sizing.py`:

```python
import pytest

from tradingsystem.risk.kelly_sizing import compute_half_kelly_target_fraction, compute_kelly_fraction


def test_compute_kelly_fraction_positive_edge():
    # f* = (0.6*2.0 - 0.4) / 2.0 = 0.8 / 2.0 = 0.4
    assert compute_kelly_fraction(win_rate=0.6, payoff_ratio=2.0) == pytest.approx(0.4)


def test_compute_kelly_fraction_negative_edge_clamps_to_zero():
    # f* = (0.3*1.0 - 0.7) / 1.0 = -0.4 -> clamped
    assert compute_kelly_fraction(win_rate=0.3, payoff_ratio=1.0) == 0.0


def test_compute_kelly_fraction_zero_payoff_ratio_returns_zero():
    assert compute_kelly_fraction(win_rate=0.6, payoff_ratio=0.0) == 0.0


def test_compute_half_kelly_insufficient_sample_returns_none():
    returns_pct = [0.10, -0.05, 0.10]  # only 3 trades
    assert compute_half_kelly_target_fraction(returns_pct, min_sample_size=10) is None


def test_compute_half_kelly_all_wins_returns_none():
    # Payoff ratio is undefined with no losses to divide by.
    returns_pct = [0.10] * 10
    assert compute_half_kelly_target_fraction(returns_pct, min_sample_size=10) is None


def test_compute_half_kelly_all_losses_returns_none():
    returns_pct = [-0.05] * 10
    assert compute_half_kelly_target_fraction(returns_pct, min_sample_size=10) is None


def test_compute_half_kelly_normal_case():
    # 6 wins @ +10%, 4 losses @ -5%: win_rate=0.6, avg_win=0.10, avg_loss=0.05,
    # payoff_ratio=2.0 -> full Kelly 0.4 -> half Kelly 0.2
    returns_pct = [0.10] * 6 + [-0.05] * 4
    assert compute_half_kelly_target_fraction(returns_pct, min_sample_size=10) == pytest.approx(0.2)


def test_compute_half_kelly_clamped_to_max_fraction():
    returns_pct = [0.10] * 6 + [-0.05] * 4  # half-Kelly would be 0.2
    assert compute_half_kelly_target_fraction(returns_pct, min_sample_size=10, max_fraction=0.1) == 0.1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_kelly_sizing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradingsystem.risk.kelly_sizing'`.

- [ ] **Step 3: Implement `risk/kelly_sizing.py`**

Create `src/tradingsystem/risk/kelly_sizing.py`:

```python
"""Fractional-Kelly position sizing from this system's own realized-trade history.

Pure, no I/O — same style as risk/stop_loss.py and risk/position_sizing.py.
Inputs (a bucket of historical % returns for one rating) are computed by
db/repositories.py's get_realized_returns_by_rating; risk/position_sizing.py
consumes this module's output in shadow mode first (see the fractional-Kelly
sizing plan's Global Constraints).
"""

from __future__ import annotations


def compute_kelly_fraction(win_rate: float, payoff_ratio: float) -> float:
    """Full Kelly fraction f* = (p*b - q) / b for a binary win/loss bet.

    p = win_rate, q = 1 - p, b = payoff_ratio (avg win size / avg loss size).
    Clamped at 0.0 (never negative) when the edge is negative — "don't
    trade" per the backlog's own framing, not a short/lever-down signal this
    long-only system supports anyway (risk/position_sizing.py).
    """
    if payoff_ratio <= 0:
        return 0.0
    loss_rate = 1.0 - win_rate
    fraction = (win_rate * payoff_ratio - loss_rate) / payoff_ratio
    return max(0.0, fraction)


def compute_half_kelly_target_fraction(
    returns_pct: list[float], min_sample_size: int, max_fraction: float = 1.0
) -> float | None:
    """Half-Kelly fraction of max_position_pct to target, from a bucket of
    historical % returns for one entry rating.

    Returns None (caller falls back to the existing flat sizing) when there
    isn't yet enough data to trust the estimate, or when every trade in the
    bucket was a win or every trade was a loss (payoff ratio undefined
    either way). Clamped to [0, max_fraction] — this fraction multiplies
    max_position_pct the same way _TARGET_FRACTION_OF_MAX does today in
    position_sizing.py, so it must never exceed the existing ceiling.
    """
    if len(returns_pct) < min_sample_size:
        return None

    wins = [r for r in returns_pct if r > 0]
    losses = [r for r in returns_pct if r <= 0]
    if not wins or not losses:
        return None

    win_rate = len(wins) / len(returns_pct)
    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    if avg_loss <= 0:
        return None
    payoff_ratio = avg_win / avg_loss

    full_kelly = compute_kelly_fraction(win_rate, payoff_ratio)
    half_kelly = full_kelly / 2.0
    return min(max_fraction, half_kelly)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_kelly_sizing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/risk/kelly_sizing.py tests/test_kelly_sizing.py
git commit -m "feat: add fractional-Kelly sizing math"
```

---

### Task 3: `get_realized_returns_by_rating` repository function

**Files:**
- Modify: `src/tradingsystem/db/repositories.py`
- Test: `tests/test_repositories.py`

**Interfaces:**
- Produces: `get_realized_returns_by_rating(session: Session, since: datetime.datetime | None = None) -> dict[str, list[float]]`, keyed `"Buy"`/`"Overweight"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_repositories.py`:

```python
import datetime
import uuid

from tradingsystem.db.models import AgentRun, Decision, RealizedPnl
from tradingsystem.db.repositories import get_realized_returns_by_rating

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def make_decision(session, rating: str) -> uuid.UUID:
    agent_run = AgentRun(
        ticker="AAPL", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="decision_recorded",
    )
    session.add(agent_run)
    session.flush()
    decision = Decision(agent_run_id=agent_run.id, rating=rating, decision="buy", reasoning_summary="test")
    session.add(decision)
    session.flush()
    return decision.id


def test_get_realized_returns_by_rating_buckets_by_entry_rating(db_session):
    buy_decision_id = make_decision(db_session, "Buy")
    overweight_decision_id = make_decision(db_session, "Overweight")

    db_session.add(RealizedPnl(
        ticker="AAPL", decision_ids=[buy_decision_id], pnl_amount=100.0,
        entry_notional=1000.0, closed_at=NOW,
    ))
    db_session.add(RealizedPnl(
        ticker="MSFT", decision_ids=[overweight_decision_id], pnl_amount=-50.0,
        entry_notional=500.0, closed_at=NOW,
    ))
    db_session.flush()

    result = get_realized_returns_by_rating(db_session)

    assert result["Buy"] == [0.1]  # 100 / 1000
    assert result["Overweight"] == [-0.1]  # -50 / 500


def test_get_realized_returns_by_rating_excludes_rows_with_no_entry_notional(db_session):
    # A row written before 2026-09-03 — entry_notional left null (the
    # ALTER TABLE only adds the column, it doesn't backfill history).
    buy_decision_id = make_decision(db_session, "Buy")
    db_session.add(RealizedPnl(
        ticker="AAPL", decision_ids=[buy_decision_id], pnl_amount=100.0,
        entry_notional=None, closed_at=NOW,
    ))
    db_session.flush()

    result = get_realized_returns_by_rating(db_session)

    assert result["Buy"] == []


def test_get_realized_returns_by_rating_excludes_sell_and_underweight_ratings(db_session):
    # Only the sell-side decision that closed the position exists here — no
    # Buy/Overweight decision in decision_ids, so nothing to bucket.
    sell_decision_id = make_decision(db_session, "Sell")
    db_session.add(RealizedPnl(
        ticker="AAPL", decision_ids=[sell_decision_id], pnl_amount=100.0,
        entry_notional=1000.0, closed_at=NOW,
    ))
    db_session.flush()

    result = get_realized_returns_by_rating(db_session)

    assert result["Buy"] == []
    assert result["Overweight"] == []


def test_get_realized_returns_by_rating_double_counts_a_mixed_entry_round_trip(db_session):
    # Documented simplification: a round-trip whose decision_ids include both
    # a Buy and an Overweight entry contributes to both buckets.
    buy_decision_id = make_decision(db_session, "Buy")
    overweight_decision_id = make_decision(db_session, "Overweight")
    sell_decision_id = make_decision(db_session, "Sell")
    db_session.add(RealizedPnl(
        ticker="AAPL", decision_ids=[buy_decision_id, overweight_decision_id, sell_decision_id],
        pnl_amount=200.0, entry_notional=2000.0, closed_at=NOW,
    ))
    db_session.flush()

    result = get_realized_returns_by_rating(db_session)

    assert result["Buy"] == [0.1]
    assert result["Overweight"] == [0.1]


def test_get_realized_returns_by_rating_since_filters_older_rows(db_session):
    buy_decision_id = make_decision(db_session, "Buy")
    db_session.add(RealizedPnl(
        ticker="AAPL", decision_ids=[buy_decision_id], pnl_amount=100.0,
        entry_notional=1000.0, closed_at=NOW - datetime.timedelta(days=30),
    ))
    db_session.flush()

    result = get_realized_returns_by_rating(db_session, since=NOW - datetime.timedelta(days=1))

    assert result["Buy"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_repositories.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_realized_returns_by_rating'`.

- [ ] **Step 3: Implement it**

In `src/tradingsystem/db/repositories.py`, update the import line (currently `from tradingsystem.db.models import CircuitBreakerEvent, Fill, Order`):

```python
from tradingsystem.db.models import CircuitBreakerEvent, Decision, Fill, Order, RealizedPnl
```

Add the function at the end of the file:

```python
def get_realized_returns_by_rating(
    session: Session, since: datetime.datetime | None = None
) -> dict[str, list[float]]:
    """% return per closed round-trip, bucketed by entry rating (Buy/Overweight).

    Used by risk/kelly_sizing.py's compute_half_kelly_target_fraction. Only
    RealizedPnl rows with a non-null entry_notional are usable (rows written
    before entry_notional existed are silently excluded, never imputed).

    Simplification: RealizedPnl.decision_ids is stored sorted by UUID string
    (execution/realized_pnl.py), not chronologically, so "the rating that
    opened the position" isn't reliably recoverable — a round-trip whose
    decision_ids include both a Buy- and an Overweight-rated decision
    contributes its return to both buckets. Documented, not a bug.
    """
    query = session.query(RealizedPnl).filter(RealizedPnl.entry_notional.isnot(None))
    if since is not None:
        query = query.filter(RealizedPnl.closed_at >= since)
    rows = query.all()

    buckets: dict[str, list[float]] = {"Buy": [], "Overweight": []}
    for row in rows:
        pct_return = float(row.pnl_amount) / float(row.entry_notional)
        entry_decisions = (
            session.query(Decision)
            .filter(Decision.id.in_(row.decision_ids), Decision.rating.in_(("Buy", "Overweight")))
            .all()
        )
        for decision in entry_decisions:
            buckets[decision.rating].append(pct_return)
    return buckets
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_repositories.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/db/repositories.py tests/test_repositories.py
git commit -m "feat: add get_realized_returns_by_rating for Kelly sizing"
```

---

### Task 4: Shadow-mode wiring into `size_order` and `cycle.py`

**Files:**
- Modify: `src/tradingsystem/config.py` (`kelly_sizing_shadow_mode`, `kelly_min_sample_size`)
- Modify: `src/tradingsystem/risk/position_sizing.py`
- Modify: `src/tradingsystem/orchestration/cycle.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`, `tests/test_position_sizing.py`, `tests/test_cycle.py`

**Interfaces:**
- Produces: `Settings().kelly_sizing_shadow_mode: bool` (default `True`); `Settings().kelly_min_sample_size: int` (default `10`). `size_order(...)` gains `kelly_target_fraction: float | None = None` and `kelly_shadow_mode: bool = True`, both optional and defaulted so every existing call site and test is unaffected unless it opts in.

- [ ] **Step 1: Add the two `Settings` fields**

In `src/tradingsystem/config.py`, add after `estimated_round_trip_cost_bps` (added by the trading-frictions plan; if that plan hasn't been applied, add after `discovery_slots_per_cycle` instead):

```python
    # Idea #02 (fractional-Kelly sizing): shadow mode logs what Half-Kelly
    # would have sized alongside the existing flat 100%/50% split without
    # acting on it. Deliberately defaults True — cutover (False) is a later,
    # separate decision made once there's enough logged shadow data to
    # compare against (see docs/superpowers/plans/2026-09-03-fractional-kelly-sizing.md).
    kelly_sizing_shadow_mode: bool = True
    # Minimum realized round-trips required in a rating's bucket before
    # risk/kelly_sizing.py trusts a Half-Kelly estimate from it; below this,
    # compute_half_kelly_target_fraction returns None and sizing falls back
    # to the flat fraction regardless of shadow_mode.
    kelly_min_sample_size: int = 10
```

Add tests to `tests/test_config.py`:

```python
def test_kelly_sizing_shadow_mode_defaults_true():
    assert Settings(_env_file=None).kelly_sizing_shadow_mode is True


def test_kelly_min_sample_size_default():
    assert Settings(_env_file=None).kelly_min_sample_size == 10
```

Add to `.env.example`, after the discovery-slots section:

```
# --- Fractional-Kelly sizing (idea #02) — shadow mode logs the Kelly-derived
# size alongside the existing flat sizing without acting on it. Flip to
# false only after reviewing enough shadow-mode data to trust it. ---
KELLY_SIZING_SHADOW_MODE=true
KELLY_MIN_SAMPLE_SIZE=10
```

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 2: Write the failing tests for `size_order`'s shadow behavior**

Add to `tests/test_position_sizing.py`:

```python
def test_buy_with_kelly_data_in_shadow_mode_still_uses_flat_sizing():
    portfolio = make_portfolio()
    result = size_order(
        "Buy", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW,
        kelly_target_fraction=0.2, kelly_shadow_mode=True,
    )
    # Flat sizing unchanged: target = 10% of 100,000 = 10,000 -> 100 shares,
    # NOT the Kelly-derived 0.2 * 10% * 100,000 = 2,000 -> 20 shares.
    assert result is not None
    assert result.qty == 100


def test_buy_with_kelly_data_and_shadow_mode_off_uses_kelly_fraction():
    portfolio = make_portfolio()
    result = size_order(
        "Buy", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW,
        kelly_target_fraction=0.2, kelly_shadow_mode=False,
    )
    # target = 0.2 * 10% * 100,000 = 2,000 -> 20 shares
    assert result is not None
    assert result.qty == 20


def test_buy_with_no_kelly_data_uses_flat_sizing_regardless_of_shadow_mode():
    portfolio = make_portfolio()
    result = size_order(
        "Buy", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW,
        kelly_target_fraction=None, kelly_shadow_mode=False,
    )
    assert result is not None
    assert result.qty == 100  # falls back to flat sizing — no Kelly estimate to use yet


def test_underweight_ignores_kelly_target_fraction():
    # Kelly only applies to entries — exits are untouched.
    portfolio = make_portfolio(position_value_by_ticker={"AAPL": 10_000.0})
    result = size_order(
        "Underweight", "AAPL", portfolio, current_price=100.0, max_position_pct=0.10, data_timestamp=NOW,
        kelly_target_fraction=0.9, kelly_shadow_mode=False,
    )
    assert result is not None
    assert result.side == "sell"
    assert result.qty == 50  # unchanged from test_underweight_reduces_to_half_current_position
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_position_sizing.py -v`
Expected: FAIL — `size_order()` doesn't accept `kelly_target_fraction`/`kelly_shadow_mode` yet. All pre-existing tests should still pass at this point (they don't pass the new kwargs), confirming the signature change alone is additive.

- [ ] **Step 4: Update `size_order`**

Replace `src/tradingsystem/risk/position_sizing.py` in full:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_position_sizing.py -v`
Expected: PASS — all pre-existing tests (which don't pass the new kwargs, so `kelly_target_fraction=None` by default, which always falls back to flat sizing) plus the 4 new ones.

- [ ] **Step 6: Wire the realized-returns lookup and Kelly fraction into `cycle.py`**

In `src/tradingsystem/orchestration/cycle.py`, update the import lines:

```python
from tradingsystem.db.repositories import get_active_breaker_event, get_realized_returns_by_rating, record_breaker_trip
from tradingsystem.risk.kelly_sizing import compute_half_kelly_target_fraction
```

In `run_full_cycle`, immediately after `risk_config = risk_config or load_risk_config()`, add:

```python
    returns_by_rating = get_realized_returns_by_rating(session)
```

In the per-ticker loop, replace the `size_order(...)` call:

```python
            portfolio = build_portfolio_state(alpaca_client)
            price = alpaca_client.get_latest_price(ticker)
            kelly_target_fraction = None
            if result.rating in ("Buy", "Overweight"):
                kelly_target_fraction = compute_half_kelly_target_fraction(
                    returns_by_rating.get(result.rating, []),
                    min_sample_size=settings.kelly_min_sample_size,
                )
            proposal = size_order(
                result.rating, ticker, portfolio, price,
                risk_config.max_position_pct, data_timestamp=datetime.datetime.utcnow(),
                kelly_target_fraction=kelly_target_fraction,
                kelly_shadow_mode=settings.kelly_sizing_shadow_mode,
            )
```

- [ ] **Step 7: Run `test_cycle.py` to verify no regressions**

Run: `pytest tests/test_cycle.py -v`
Expected: PASS, unmodified. Every test in this file uses a fresh `db_session` with no pre-existing `RealizedPnl` rows, so `get_realized_returns_by_rating` always returns `{"Buy": [], "Overweight": []}`, `compute_half_kelly_target_fraction([], min_sample_size=10)` always returns `None` (sample size 0 < 10), and `size_order` therefore always falls back to flat sizing — identical to today's behavior with zero trade history, which is exactly the state a real system starts in too.

- [ ] **Step 8: Add one `test_cycle.py` test proving the shadow log line fires once there's history**

Append to `tests/test_cycle.py`:

```python
def test_buy_decision_logs_kelly_shadow_comparison_once_enough_history_exists(db_session, monkeypatch, caplog):
    from tradingsystem.db.models import AgentRun as _AgentRun
    from tradingsystem.db.models import Decision as _Decision
    from tradingsystem.db.models import RealizedPnl as _RealizedPnl

    # Seed 10 realized round-trips rated "Buy" — 6 wins @ +10%, 4 losses @
    # -5% — enough to clear kelly_min_sample_size=10 (the Settings default).
    for i in range(10):
        run = _AgentRun(
            ticker="AAPL", run_type="pre_market", started_at=datetime.datetime(2026, 1, 1),
            finished_at=datetime.datetime(2026, 1, 1), market_status="open", outcome="decision_recorded",
        )
        db_session.add(run)
        db_session.flush()
        decision = _Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="seed")
        db_session.add(decision)
        db_session.flush()
        pnl = 100.0 if i < 6 else -50.0
        db_session.add(_RealizedPnl(
            ticker="AAPL", decision_ids=[decision.id], pnl_amount=pnl,
            entry_notional=1000.0, closed_at=datetime.datetime(2026, 1, 2),
        ))
    db_session.flush()

    ScriptedGraph.calls = [(make_final_state("Buy: strong fundamentals"), "Buy")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)
    from tradingsystem.execution.alpaca_client import SubmittedOrder
    client = FakeAlpacaClient(market_status="open", equity=100_000.0, price=100.0,
                               submit_response=SubmittedOrder(alpaca_order_id="kelly1", status="new"))

    with caplog.at_level("INFO"):
        cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=["AAPL"])

    assert any("Kelly shadow sizing for AAPL Buy" in record.message for record in caplog.records)
    # Shadow mode: the actual order still used flat sizing (100 shares), not
    # the Kelly-derived alternative.
    assert client.submit_calls == [("AAPL", "buy", 100, 100.0)]
```

- [ ] **Step 9: Run the full test suite to check for regressions**

Run: `pytest -v`
Expected: no new failures.

- [ ] **Step 10: Commit**

```bash
git add src/tradingsystem/config.py src/tradingsystem/risk/position_sizing.py src/tradingsystem/orchestration/cycle.py .env.example tests/test_config.py tests/test_position_sizing.py tests/test_cycle.py
git commit -m "feat: shadow-run fractional-Kelly sizing alongside the flat split"
```

---

## Self-Review

**1. Spec coverage:** Idea #02 asks to (a) replace flat sizing with Half-Kelly derived from `TradingMemoryEntry`-adjacent realized-return data, (b) keep the existing 10%-of-equity cap as the ceiling, and (c) shadow-run before switching over. This plan reinterprets (a)'s data source correctly — `TradingMemoryEntry` (the backlog's cited table) turns out to hold unstructured TradingAgents reflection text, not structured returns; `RealizedPnl` (joined through `Decision.rating`) is the table that actually has what's needed, once `entry_notional` is added (Task 1). (b) is preserved — `kelly_target_fraction` only ever multiplies `max_position_pct`, same as the flat fraction does today. (c) is Task 4's entire design: shadow mode is the only mode this plan turns on.

**2. Placeholder scan:** No TBDs. The `decision_ids`-ordering limitation (can't recover which decision chronologically opened a position) is a documented, tested approximation (Task 3's "double-counts a mixed entry round trip" test), not a stub.

**3. Type consistency:** `RealizedPnlResult.entry_notional: float` (Task 1) → `RealizedPnl.entry_notional: float | None` (Task 1, nullable in the DB since old rows lack it) → `get_realized_returns_by_rating(...) -> dict[str, list[float]]` (Task 3, filters out the nulls) → `compute_half_kelly_target_fraction(returns_pct: list[float], ...) -> float | None` (Task 2) → `size_order(..., kelly_target_fraction: float | None, ...)` (Task 4). Every hand-off matches.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-03-fractional-kelly-sizing.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
