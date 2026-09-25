# Discovery Screen Earnings-Proximity Factor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the discovery screen a third, free, cheap ranking factor — days until next earnings — so the candidate universe isn't ranked purely on momentum and relative volume, the two cheapest technical signals available.

**Architecture:** `orchestration/ticker_selection.py:rank_candidates()` currently sums two cross-sectional ranks (momentum, relative volume) into a composite score. This plan adds a third: `compute_earnings_proximity_score` scores how close a ticker is to its next earnings date, and `rank_candidates` gains an **optional** `earnings_days_by_ticker` parameter that folds a third rank into the composite only when supplied — omitting it (the default) reproduces the exact original 2-factor ranking, so this is purely additive and cannot silently reorder existing behavior. The actual earnings-date lookup (`fetch_earnings_proximity_days`, new) is a best-effort, fail-open-per-ticker `yfinance` call, mirroring the same resilience pattern the pinned TradingAgents package already uses for its own yfinance-backed `resolve_instrument_identity` (`.venv/Lib/site-packages/tradingagents/agents/utils/agent_utils.py:79-99`: "best-effort by design... if yfinance is unavailable, rate-limited, or doesn't recognise the ticker, we return `{}`"). `build_cycle_ticker_list` wires the two together.

Scope note: the backlog's idea #01 also mentions an "insider-buying cluster" factor as a second possible addition. That one needs a second external data integration (Alpha Vantage's `INSIDER_TRANSACTIONS` endpoint returns unstructured-enough data that a real numeric signal would be a second, separately-scoped piece of work) — it is deliberately left out of this plan to keep this a "medium effort" single-factor change rather than bundling two new data sources into one plan. It's a natural follow-on once this factor's shape has proven out.

**Tech Stack:** Python 3.11, `yfinance` (already an installed transitive dependency via `tradingagents`; this plan adds it as an explicit direct dependency since our own code now imports it directly), pytest.

**Spec:** The Alpha Backlog (idea #01, "Give the discovery screen something to look at besides price and volume"), published artifact `https://claude.ai/code/artifact/58853951-b14c-4bfd-9d7e-97d14c43b033`. This plan document is self-contained; the artifact is provenance only.

## Global Constraints

- `rank_candidates`'s existing signature and behavior must be preserved for callers that don't pass the new parameter — this file's 6 existing tests must all pass unmodified.
- The earnings-date lookup must never raise out of `fetch_earnings_proximity_days` for a single ticker's failure — confirmed from reading `yfinance`'s own `Quote._fetch_calendar` (installed version 1.7.0): it can return `{}` on a fetch failure, or omit the `"Earnings Date"` key entirely, or (rarely) raise `YFDataException` — this function must handle all three by recording `None` for that ticker and continuing, the same "one bad ticker never blocks the whole screen" principle `ticker_selection.py`'s own module docstring already establishes.
- `yfinance.Ticker(ticker).calendar` returns a plain `dict`; when present, `calendar["Earnings Date"]` is a `list[datetime.date]` (plain dates, not datetimes) — confirmed by reading the installed package's `yfinance/scrapers/quote.py:Quote._fetch_calendar` source directly, not assumed.

---

### Task 1: `compute_earnings_proximity_score` (pure)

**Files:**
- Modify: `src/tradingsystem/orchestration/ticker_selection.py`
- Test: `tests/test_ticker_selection.py`

**Interfaces:**
- Produces: `compute_earnings_proximity_score(days_until: int | None, horizon_days: int = 10) -> float`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ticker_selection.py` (extend the existing import from `tradingsystem.orchestration.ticker_selection`):

```python
def test_compute_earnings_proximity_score_near_term_returns_days_until():
    assert compute_earnings_proximity_score(3) == 3.0
    assert compute_earnings_proximity_score(0) == 0.0


def test_compute_earnings_proximity_score_none_returns_neutral_baseline():
    assert compute_earnings_proximity_score(None, horizon_days=10) == 10.0


def test_compute_earnings_proximity_score_beyond_horizon_returns_neutral_baseline():
    assert compute_earnings_proximity_score(45, horizon_days=10) == 10.0


def test_compute_earnings_proximity_score_negative_returns_neutral_baseline():
    # A stale/already-passed date the calendar hasn't refreshed yet — treat as
    # no usable signal rather than ranking it as maximally "close."
    assert compute_earnings_proximity_score(-2, horizon_days=10) == 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ticker_selection.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_earnings_proximity_score'`.

- [ ] **Step 3: Implement it**

In `src/tradingsystem/orchestration/ticker_selection.py`, add after `compute_relative_volume` (after line 35):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ticker_selection.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/orchestration/ticker_selection.py tests/test_ticker_selection.py
git commit -m "feat: add earnings-proximity scoring function"
```

---

### Task 2: Fold the earnings factor into `rank_candidates`, additively

**Files:**
- Modify: `src/tradingsystem/orchestration/ticker_selection.py`
- Test: `tests/test_ticker_selection.py`

**Interfaces:**
- Produces: `rank_candidates(bars_by_ticker: dict[str, DailyBars], earnings_days_by_ticker: dict[str, int | None] | None = None) -> list[str]` — same return type and default-call behavior as before; the new parameter is additive-only.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ticker_selection.py`, after the existing `test_rank_candidates_ties_break_alphabetically`:

```python
def test_rank_candidates_without_earnings_data_matches_original_two_factor_ranking():
    bars = {
        "AAPL": DailyBars(ticker="AAPL", closes=[100.0, 110.0], volumes=[100.0, 100.0]),
        "MSFT": DailyBars(ticker="MSFT", closes=[100.0, 100.0], volumes=[100.0, 100.0]),
        "NVDA": DailyBars(ticker="NVDA", closes=[100.0, 90.0], volumes=[100.0, 100.0]),
    }
    # Omitting earnings_days_by_ticker entirely must reproduce the exact
    # 2-factor result from test_rank_candidates_orders_best_first.
    assert rank_candidates(bars) == ["AAPL", "MSFT", "NVDA"]
    # An empty dict must behave identically to omitting it.
    assert rank_candidates(bars, {}) == ["AAPL", "MSFT", "NVDA"]


def test_rank_candidates_earnings_proximity_can_promote_a_momentum_laggard():
    # MSFT and NVDA are momentum/volume-tied (both flat); NVDA has earnings
    # tomorrow, MSFT has none scheduled — NVDA should rank ahead of MSFT.
    bars = {
        "MSFT": DailyBars(ticker="MSFT", closes=[100.0, 100.0], volumes=[100.0, 100.0]),
        "NVDA": DailyBars(ticker="NVDA", closes=[100.0, 100.0], volumes=[100.0, 100.0]),
    }
    earnings_days = {"MSFT": None, "NVDA": 1}
    assert rank_candidates(bars, earnings_days) == ["NVDA", "MSFT"]


def test_rank_candidates_earnings_beyond_horizon_does_not_affect_tied_candidates():
    bars = {
        "MSFT": DailyBars(ticker="MSFT", closes=[100.0, 100.0], volumes=[100.0, 100.0]),
        "NVDA": DailyBars(ticker="NVDA", closes=[100.0, 100.0], volumes=[100.0, 100.0]),
    }
    # Both beyond the 10-day horizon -> both collapse to the same neutral
    # score -> falls through to the existing alphabetical tiebreak.
    earnings_days = {"MSFT": 60, "NVDA": 90}
    assert rank_candidates(bars, earnings_days) == ["MSFT", "NVDA"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ticker_selection.py -v`
Expected: FAIL — `rank_candidates()` doesn't accept a second positional argument yet.

- [ ] **Step 3: Update `rank_candidates`**

Replace `rank_candidates` in `src/tradingsystem/orchestration/ticker_selection.py` (current lines 38-52) with:

```python
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

    momentum_order = sorted(momentum, key=lambda t: (-momentum[t], t))
    volume_order = sorted(rel_vol, key=lambda t: (-rel_vol[t], t))
    momentum_rank = {t: i for i, t in enumerate(momentum_order)}
    volume_rank = {t: i for i, t in enumerate(volume_order)}

    composite = {t: momentum_rank[t] + volume_rank[t] for t in bars_by_ticker}

    if earnings_days_by_ticker:
        earnings_score = {
            t: compute_earnings_proximity_score(earnings_days_by_ticker.get(t))
            for t in bars_by_ticker
        }
        earnings_order = sorted(earnings_score, key=lambda t: (earnings_score[t], t))
        earnings_rank = {t: i for i, t in enumerate(earnings_order)}
        composite = {t: composite[t] + earnings_rank[t] for t in bars_by_ticker}

    return sorted(composite, key=lambda t: (composite[t], t))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ticker_selection.py -v`
Expected: PASS, including all pre-existing `rank_candidates` tests (unmodified, still called with one positional argument).

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/orchestration/ticker_selection.py tests/test_ticker_selection.py
git commit -m "feat: fold an optional earnings-proximity rank into rank_candidates"
```

---

### Task 3: `fetch_earnings_proximity_days` and wiring into `build_cycle_ticker_list`

**Files:**
- Modify: `src/tradingsystem/orchestration/ticker_selection.py`
- Modify: `pyproject.toml` (add `yfinance` as an explicit direct dependency)
- Test: `tests/test_ticker_selection.py`

**Interfaces:**
- Produces: `fetch_earnings_proximity_days(tickers: list[str], as_of: datetime.date) -> dict[str, int | None]`.
- `build_cycle_ticker_list`'s public signature is unchanged; internally it now also calls `fetch_earnings_proximity_days` and threads the result into `rank_candidates`.

- [ ] **Step 1: Add `yfinance` as an explicit dependency**

`yfinance` is already installed (a transitive dependency of the pinned `tradingagents` package), but `ticker_selection.py` is about to import it directly in our own code — declare that explicitly rather than relying on an incidental transitive install. In `pyproject.toml`, add to the `dependencies` list (after `"ruamel.yaml>=0.18",`, before the `tradingagents` line):

```
    "yfinance>=1.7",
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_ticker_selection.py`:

```python
import datetime


class _FakeCalendarTicker:
    def __init__(self, calendar):
        self._calendar = calendar

    @property
    def calendar(self):
        if isinstance(self._calendar, Exception):
            raise self._calendar
        return self._calendar


def test_fetch_earnings_proximity_days_computes_days_until_next_earnings(monkeypatch):
    import tradingsystem.orchestration.ticker_selection as ticker_selection_module

    as_of = datetime.date(2026, 1, 1)
    fake_tickers = {
        "AAPL": _FakeCalendarTicker({"Earnings Date": [datetime.date(2026, 1, 4)]}),
        "MSFT": _FakeCalendarTicker({}),  # no calendar data available
    }
    monkeypatch.setattr(
        ticker_selection_module.yf, "Ticker", lambda t: fake_tickers[t]
    )

    result = ticker_selection_module.fetch_earnings_proximity_days(["AAPL", "MSFT"], as_of)

    assert result == {"AAPL": 3, "MSFT": None}


def test_fetch_earnings_proximity_days_picks_the_nearest_future_date(monkeypatch):
    import tradingsystem.orchestration.ticker_selection as ticker_selection_module

    as_of = datetime.date(2026, 1, 1)
    fake_ticker = _FakeCalendarTicker(
        {"Earnings Date": [datetime.date(2026, 1, 10), datetime.date(2026, 1, 5)]}
    )
    monkeypatch.setattr(ticker_selection_module.yf, "Ticker", lambda t: fake_ticker)

    result = ticker_selection_module.fetch_earnings_proximity_days(["AAPL"], as_of)

    assert result == {"AAPL": 4}


def test_fetch_earnings_proximity_days_ignores_past_dates(monkeypatch):
    import tradingsystem.orchestration.ticker_selection as ticker_selection_module

    as_of = datetime.date(2026, 1, 10)
    fake_ticker = _FakeCalendarTicker({"Earnings Date": [datetime.date(2026, 1, 5)]})
    monkeypatch.setattr(ticker_selection_module.yf, "Ticker", lambda t: fake_ticker)

    result = ticker_selection_module.fetch_earnings_proximity_days(["AAPL"], as_of)

    assert result == {"AAPL": None}


def test_fetch_earnings_proximity_days_one_ticker_failure_does_not_block_others(monkeypatch):
    import tradingsystem.orchestration.ticker_selection as ticker_selection_module

    as_of = datetime.date(2026, 1, 1)
    fake_tickers = {
        "AAPL": _FakeCalendarTicker(RuntimeError("simulated yfinance failure")),
        "MSFT": _FakeCalendarTicker({"Earnings Date": [datetime.date(2026, 1, 2)]}),
    }
    monkeypatch.setattr(
        ticker_selection_module.yf, "Ticker", lambda t: fake_tickers[t]
    )

    result = ticker_selection_module.fetch_earnings_proximity_days(["AAPL", "MSFT"], as_of)

    assert result == {"AAPL": None, "MSFT": 1}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_ticker_selection.py -v`
Expected: FAIL — `ticker_selection_module.yf` and `fetch_earnings_proximity_days` don't exist yet.

- [ ] **Step 4: Implement `fetch_earnings_proximity_days`**

In `src/tradingsystem/orchestration/ticker_selection.py`, add to the top-of-file imports (the module currently has only `import logging` and the `tradingsystem.execution.alpaca_client` import):

```python
import datetime

import yfinance as yf
```

Add the function after `build_cycle_ticker_list` (at the end of the file, after line 78):

```python
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
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `pytest tests/test_ticker_selection.py -v`
Expected: PASS.

- [ ] **Step 6: Wire it into `build_cycle_ticker_list`**

Replace `build_cycle_ticker_list` (current lines 69-78):

```python
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
```

- [ ] **Step 7: Prevent the existing `build_cycle_ticker_list` test from making a real network call**

`test_build_cycle_ticker_list_combines_held_and_discovery` in `tests/test_ticker_selection.py` calls `build_cycle_ticker_list` without mocking anything earnings-related — it will now try a real `yfinance` call. Add a `monkeypatch` fixture parameter and stub the fetch:

Change the test's signature and body — find:

```python
def test_build_cycle_ticker_list_combines_held_and_discovery():
    client = FakeAlpacaClientForSelection(
```

Replace with:

```python
def test_build_cycle_ticker_list_combines_held_and_discovery(monkeypatch):
    import tradingsystem.orchestration.ticker_selection as ticker_selection_module

    monkeypatch.setattr(
        ticker_selection_module, "fetch_earnings_proximity_days", lambda tickers, as_of: {}
    )
    client = FakeAlpacaClientForSelection(
```

(The rest of the test body is unchanged — stubbing the fetch to return `{}` reproduces the pre-existing 2-factor ranking exactly, per Task 2's backward-compatibility guarantee, so the test's existing assertions still hold.)

- [ ] **Step 8: Run the full ticker-selection test file, then the full suite**

Run: `pytest tests/test_ticker_selection.py -v`
Expected: PASS, all tests, no network calls made.

Run: `pytest -v`
Expected: no new failures.

- [ ] **Step 9: Install the new dependency and commit**

```bash
pip install -e .
git add src/tradingsystem/orchestration/ticker_selection.py pyproject.toml tests/test_ticker_selection.py
git commit -m "feat: wire earnings-proximity data into the discovery screen"
```

---

## Self-Review

**1. Spec coverage:** Idea #01 asks for "even one or two additional cheap, free factors" folded into the composite rank so the discovery screen isn't purely momentum/volume. This plan delivers one (earnings proximity), fully wired end-to-end, and explicitly scopes the second (insider-buying cluster) out with a stated reason (needs a second, separately-designed data integration).

**2. Placeholder scan:** No TBDs. `fetch_earnings_proximity_days`'s three failure modes (empty calendar, missing key, raised exception) are all handled and each has a dedicated test.

**3. Type consistency:** `compute_earnings_proximity_score(days_until: int | None, horizon_days: int = 10) -> float` (Task 1) is called from `rank_candidates` (Task 2) with `earnings_days_by_ticker.get(t)` (an `int | None`), matching. `fetch_earnings_proximity_days(...) -> dict[str, int | None]` (Task 3) produces exactly the type `rank_candidates`'s `earnings_days_by_ticker` parameter expects.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-03-discovery-screen-earnings-factor.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
