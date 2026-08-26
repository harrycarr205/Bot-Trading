# Dynamic Ticker Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static, always-analyzed `config/watchlist.yaml` with a dynamic per-cycle ticker list: every held position is always reassessed (uncapped), plus a configurable number of discovery slots filled from a larger candidate universe via a deterministic momentum + relative-volume screen.

**Architecture:** A new pure module (`orchestration/ticker_selection.py`) ranks candidates on cross-sectional momentum + relative-volume rank-sum (no LLM involved), then a thin composition function combines that ranking with live held positions (from `AlpacaClient.get_position_details()`, already built) to produce the cycle's ticker list. Wired into `cycle.py` in place of the static watchlist load, before the market-status check.

**Tech Stack:** Python 3.12, alpaca-py's `StockHistoricalDataClient.get_stock_bars` for historical daily bars, pytest against the dedicated `trading_test` database.

**Spec:** `docs/superpowers/specs/2026-08-25-ticker-selection-design.md`

## Global Constraints

- No DB schema change in this plan.
- Bars requested from Alpaca always end at the most recently *completed* trading day (never today's partial bar) — this system's cycles run at ~9:35am and 12:30pm ET, both mid-trading-day, so comparing partial current-day volume against full historical averages would make relative volume look artificially low.
- Held tickers (from live Alpaca position data) are never capped by `discovery_slots_per_cycle` — that setting governs discovery candidates only.
- `discovery_slots_per_cycle` defaults to `4`, `.env`-configurable as `DISCOVERY_SLOTS_PER_CYCLE`.
- Ties in candidate ranking are broken alphabetically by ticker, for determinism.
- A candidate Alpaca can't return bars for is simply omitted from ranking (logged, not a crash).
- Tests run via `.venv/Scripts/python.exe -m pytest`, against `trading_test` (the `db_session` fixture in `tests/conftest.py`), never the live `trading` database.

---

### Task 1: Rename watchlist config to candidate universe, add discovery-slots setting

**Files:**
- Modify: `src/tradingsystem/config.py`
- Create: `config/candidate_universe.yaml`
- Delete: `config/watchlist.yaml`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `CandidateUniverseConfig` (pydantic `BaseModel`, field `tickers: list[str]`), `load_candidate_universe(path: Path = REPO_ROOT / "config" / "candidate_universe.yaml") -> CandidateUniverseConfig`, `Settings.discovery_slots_per_cycle: int` (default `4`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
from tradingsystem.config import Settings, load_candidate_universe


def test_load_candidate_universe_reads_real_file():
    universe = load_candidate_universe()
    assert "AAPL" in universe.tickers
    assert "SPY" in universe.tickers
    assert "QQQ" in universe.tickers
    assert len(universe.tickers) >= 30


def test_discovery_slots_per_cycle_default():
    assert Settings().discovery_slots_per_cycle == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_candidate_universe'`

- [ ] **Step 3: Rename the config model/loader and add the new setting**

In `src/tradingsystem/config.py`, replace:

```python
    kill_switch_file: str = str(REPO_ROOT / "KILL_SWITCH")
```

with:

```python
    # Discovery slots for orchestration/ticker_selection.py's screen — held
    # positions are always reassessed on top of this, uncapped (see
    # docs/superpowers/specs/2026-08-25-ticker-selection-design.md).
    discovery_slots_per_cycle: int = 4

    kill_switch_file: str = str(REPO_ROOT / "KILL_SWITCH")
```

Replace:

```python
class WatchlistConfig(BaseModel):
    tickers: list[str]
```

with:

```python
class CandidateUniverseConfig(BaseModel):
    tickers: list[str]
```

Replace:

```python
def load_watchlist(path: Path = REPO_ROOT / "config" / "watchlist.yaml") -> WatchlistConfig:
    data = yaml.safe_load(path.read_text())
    return WatchlistConfig.model_validate(data)
```

with:

```python
def load_candidate_universe(
    path: Path = REPO_ROOT / "config" / "candidate_universe.yaml",
) -> CandidateUniverseConfig:
    data = yaml.safe_load(path.read_text())
    return CandidateUniverseConfig.model_validate(data)
```

- [ ] **Step 4: Create the renamed config file with the agreed starting universe**

Create `config/candidate_universe.yaml` (delete `config/watchlist.yaml` — `git rm config/watchlist.yaml`):

```yaml
# The pool orchestration/ticker_selection.py's discovery screen draws
# candidates from. Freely editable — add/remove tickers here, takes effect
# next cycle. No code change required. You're responsible for judgment
# calls on a ticker's liquidity/coverage; the system doesn't gate this
# automatically. This does NOT limit which tickers get analyzed: any
# currently-held position is always reassessed regardless of whether it's
# still listed here (see the design spec).
tickers:
  # Tech
  - AAPL
  - MSFT
  - NVDA
  - GOOGL
  - META
  - CRM
  # Healthcare
  - UNH
  - JNJ
  - LLY
  - ABBV
  # Financials
  - JPM
  - BAC
  - GS
  - V
  # Consumer discretionary
  - AMZN
  - HD
  - NKE
  - MCD
  # Consumer staples
  - PG
  - KO
  - WMT
  - COST
  # Energy
  - XOM
  - CVX
  # Industrials
  - CAT
  - BA
  - HON
  # Communication services
  - NFLX
  - DIS
  # Utilities
  - NEE
  - DUK
  # Materials
  - LIN
  # Real estate
  - PLD
  # Broad-market index ETFs
  - SPY   # S&P 500
  - QQQ   # Nasdaq-100
  - DIA   # Dow Jones Industrial Average
  - IWM   # Russell 2000 (small-cap)
  - VTI   # total US stock market
  - VXUS  # total international (ex-US)
  - VT    # total world stock market
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/config.py config/candidate_universe.yaml tests/test_config.py
git rm config/watchlist.yaml
git commit -m "Rename watchlist config to candidate universe, add discovery_slots_per_cycle"
```

---

### Task 2: `AlpacaClient` — fetch recent daily bars

**Files:**
- Modify: `src/tradingsystem/execution/alpaca_client.py`

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `DailyBars` (frozen dataclass: `ticker: str`, `closes: list[float]` oldest-to-newest, `volumes: list[float]` oldest-to-newest), `AlpacaClientProtocol.get_recent_daily_bars(tickers: list[str], lookback_days: int) -> dict[str, DailyBars]` (also implemented on the real `AlpacaClient`). A ticker Alpaca returns fewer than 2 bars for is simply omitted from the result dict.

No dedicated unit test for this step — matches this file's existing convention (`get_positions`, `submit_limit_order`, `get_order`, `get_position_details`, etc. are all thin Alpaca-API wrappers with no direct unit test; only pure helpers like `round_to_tick` get one). It's exercised indirectly via Task 4's `FakeAlpacaClient`-based tests.

- [ ] **Step 1: Add the new imports**

In `src/tradingsystem/execution/alpaca_client.py`, replace:

```python
from __future__ import annotations

import dataclasses
import datetime
from typing import Protocol

from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from tradingsystem.config import Settings
```

with:

```python
from __future__ import annotations

import dataclasses
import datetime
import zoneinfo
from typing import Protocol

from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from tradingsystem.config import Settings

_NY = zoneinfo.ZoneInfo("America/New_York")
```

- [ ] **Step 2: Add the `DailyBars` dataclass**

Replace:

```python
@dataclasses.dataclass(frozen=True)
class PositionDetail:
    ticker: str
    qty: float
    avg_entry_price: float
    current_price: float
```

with:

```python
@dataclasses.dataclass(frozen=True)
class PositionDetail:
    ticker: str
    qty: float
    avg_entry_price: float
    current_price: float


@dataclasses.dataclass(frozen=True)
class DailyBars:
    ticker: str
    closes: list[float]   # oldest to newest
    volumes: list[float]  # oldest to newest, same ordering as closes
```

- [ ] **Step 3: Add the protocol method**

Replace:

```python
class AlpacaClientProtocol(Protocol):
    def get_account(self) -> AccountSnapshot: ...
    def get_positions(self) -> dict[str, float]: ...
    def get_position_details(self) -> list[PositionDetail]: ...
    def get_open_orders(self) -> set[tuple[str, str]]: ...
```

with:

```python
class AlpacaClientProtocol(Protocol):
    def get_account(self) -> AccountSnapshot: ...
    def get_positions(self) -> dict[str, float]: ...
    def get_position_details(self) -> list[PositionDetail]: ...
    def get_recent_daily_bars(self, tickers: list[str], lookback_days: int) -> dict[str, DailyBars]: ...
    def get_open_orders(self) -> set[tuple[str, str]]: ...
```

- [ ] **Step 4: Implement it on the real `AlpacaClient`**

Replace:

```python
    def get_open_orders(self) -> set[tuple[str, str]]:
        orders = self._client.get_orders()
        return {(o.symbol, o.side.value) for o in orders}
```

with:

```python
    def get_recent_daily_bars(self, tickers: list[str], lookback_days: int) -> dict[str, DailyBars]:
        # Always end at the most recently completed trading day — never
        # today's still-forming bar, which would understate relative volume
        # at this system's actual run times (9:35am/12:30pm ET, both
        # mid-trading-day).
        end_date = datetime.datetime.now(_NY).date() - datetime.timedelta(days=1)
        end = datetime.datetime.combine(end_date, datetime.time.min, tzinfo=_NY)
        start = end - datetime.timedelta(days=lookback_days * 2)  # padding for weekends/holidays
        request = StockBarsRequest(
            symbol_or_symbols=tickers,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
        )
        barset = self._data_client.get_stock_bars(request)
        result: dict[str, DailyBars] = {}
        for ticker, bars in barset.data.items():
            trimmed = bars[-lookback_days:]
            if len(trimmed) < 2:
                continue
            result[ticker] = DailyBars(
                ticker=ticker,
                closes=[float(b.close) for b in trimmed],
                volumes=[float(b.volume) for b in trimmed],
            )
        return result

    def get_open_orders(self) -> set[tuple[str, str]]:
        orders = self._client.get_orders()
        return {(o.symbol, o.side.value) for o in orders}
```

- [ ] **Step 5: Run the full suite to confirm nothing broke**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all existing tests still PASS (this step is purely additive — no existing behavior changed).

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/execution/alpaca_client.py
git commit -m "Add AlpacaClient.get_recent_daily_bars for ticker-selection screening"
```

---

### Task 3: Pure ranking/selection functions

**Files:**
- Create: `src/tradingsystem/orchestration/ticker_selection.py`
- Test: `tests/test_ticker_selection.py`

**Interfaces:**
- Consumes: `DailyBars` from Task 2 (`from tradingsystem.execution.alpaca_client import DailyBars`).
- Produces: `compute_momentum_pct(closes: list[float]) -> float`, `compute_relative_volume(volumes: list[float]) -> float`, `rank_candidates(bars_by_ticker: dict[str, DailyBars]) -> list[str]` (best-to-worst), `select_tickers_for_cycle(held_tickers: set[str], ranked_candidates: list[str], discovery_slots: int) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ticker_selection.py`:

```python
from tradingsystem.execution.alpaca_client import DailyBars
from tradingsystem.orchestration.ticker_selection import (
    compute_momentum_pct,
    compute_relative_volume,
    rank_candidates,
    select_tickers_for_cycle,
)


def test_compute_momentum_pct_positive():
    assert compute_momentum_pct([100.0, 105.0, 110.0]) == 0.1


def test_compute_momentum_pct_negative():
    assert compute_momentum_pct([100.0, 95.0, 90.0]) == -0.1


def test_compute_momentum_pct_insufficient_data_returns_zero():
    assert compute_momentum_pct([100.0]) == 0.0
    assert compute_momentum_pct([]) == 0.0


def test_compute_relative_volume_spike():
    # baseline avg = (100+100+100)/3 = 100, most recent = 300 -> 3.0x
    assert compute_relative_volume([100.0, 100.0, 100.0, 300.0]) == 3.0


def test_compute_relative_volume_insufficient_data_returns_neutral():
    assert compute_relative_volume([100.0]) == 1.0
    assert compute_relative_volume([]) == 1.0


def test_rank_candidates_orders_best_first():
    bars = {
        "AAPL": DailyBars(ticker="AAPL", closes=[100.0, 110.0], volumes=[100.0, 100.0]),  # strong momentum, flat volume
        "MSFT": DailyBars(ticker="MSFT", closes=[100.0, 100.0], volumes=[100.0, 100.0]),  # flat momentum, flat volume
        "NVDA": DailyBars(ticker="NVDA", closes=[100.0, 90.0], volumes=[100.0, 100.0]),   # negative momentum, flat volume
    }
    ranked = rank_candidates(bars)
    assert ranked == ["AAPL", "MSFT", "NVDA"]


def test_rank_candidates_ties_break_alphabetically():
    bars = {
        "ZETA": DailyBars(ticker="ZETA", closes=[100.0, 100.0], volumes=[100.0, 100.0]),
        "ALPHA": DailyBars(ticker="ALPHA", closes=[100.0, 100.0], volumes=[100.0, 100.0]),
    }
    ranked = rank_candidates(bars)
    assert ranked == ["ALPHA", "ZETA"]


def test_select_tickers_for_cycle_held_positions_are_uncapped():
    held = {"AAPL", "MSFT", "GOOGL", "META", "NVDA"}  # 5 held, more than discovery_slots
    ranked = ["XOM", "CVX"]
    result = select_tickers_for_cycle(held, ranked, discovery_slots=2)
    assert result == sorted(held | {"XOM", "CVX"})


def test_select_tickers_for_cycle_discovery_excludes_already_held():
    held = {"AAPL"}
    ranked = ["AAPL", "MSFT", "GOOGL"]  # AAPL is top-ranked but already held
    result = select_tickers_for_cycle(held, ranked, discovery_slots=1)
    assert result == sorted({"AAPL", "MSFT"})  # MSFT is the next-best not-held candidate


def test_select_tickers_for_cycle_zero_discovery_slots():
    held = {"AAPL"}
    ranked = ["MSFT", "GOOGL"]
    result = select_tickers_for_cycle(held, ranked, discovery_slots=0)
    assert result == ["AAPL"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ticker_selection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingsystem.orchestration.ticker_selection'`

- [ ] **Step 3: Implement the pure functions**

Create `src/tradingsystem/orchestration/ticker_selection.py`:

```python
"""Deterministic per-cycle ticker selection — ARCHITECTURE.md §2/§4.

Held positions are always reassessed, uncapped; a discovery screen fills
a configurable number of additional slots from a wider candidate universe.
See docs/superpowers/specs/2026-08-25-ticker-selection-design.md.

Pure ranking/selection functions, no I/O — same style as risk/stop_loss.py
and risk/circuit_breaker.py.
"""

from __future__ import annotations

from tradingsystem.execution.alpaca_client import DailyBars


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ticker_selection.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/orchestration/ticker_selection.py tests/test_ticker_selection.py
git commit -m "Add pure momentum/volume ranking and ticker-selection functions"
```

---

### Task 4: Composition function, cycle.py wiring, and README update

**Files:**
- Modify: `src/tradingsystem/orchestration/ticker_selection.py`
- Modify: `src/tradingsystem/orchestration/cycle.py`
- Modify: `tests/test_cycle.py`
- Modify: `README.md`
- Test: `tests/test_ticker_selection.py`

**Interfaces:**
- Consumes: `rank_candidates`, `select_tickers_for_cycle` (Task 3); `AlpacaClientProtocol`, `DailyBars`, `PositionDetail` (Task 2); `load_candidate_universe`, `Settings.discovery_slots_per_cycle` (Task 1).
- Produces: `build_cycle_ticker_list(alpaca_client: AlpacaClientProtocol, candidate_universe: list[str], discovery_slots: int, lookback_days: int = 21) -> list[str]`. `run_full_cycle` gains a new `candidate_universe: list[str] | None = None` parameter.

- [ ] **Step 1: Write the failing test for the composition function**

Append to `tests/test_ticker_selection.py`:

```python
from tradingsystem.execution.alpaca_client import PositionDetail
from tradingsystem.orchestration.ticker_selection import build_cycle_ticker_list


class FakeAlpacaClientForSelection:
    def __init__(self, positions=None, bars=None):
        self.positions = positions or []
        self.bars = bars or {}

    def get_position_details(self):
        return self.positions

    def get_recent_daily_bars(self, tickers, lookback_days):
        return {t: self.bars[t] for t in tickers if t in self.bars}


def test_build_cycle_ticker_list_combines_held_and_discovery():
    client = FakeAlpacaClientForSelection(
        positions=[PositionDetail(ticker="AAPL", qty=10, avg_entry_price=100.0, current_price=110.0)],
        bars={
            "MSFT": DailyBars(ticker="MSFT", closes=[100.0, 120.0], volumes=[100.0, 100.0]),  # strong momentum
            "NVDA": DailyBars(ticker="NVDA", closes=[100.0, 90.0], volumes=[100.0, 100.0]),   # weak momentum
        },
    )

    result = build_cycle_ticker_list(client, ["MSFT", "NVDA"], discovery_slots=1)

    assert result == ["AAPL", "MSFT"]  # AAPL held (uncapped); MSFT is the top-ranked discovery pick
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ticker_selection.py::test_build_cycle_ticker_list_combines_held_and_discovery -v`
Expected: FAIL — `ImportError: cannot import name 'build_cycle_ticker_list'`

- [ ] **Step 3: Implement the composition function**

Append to `src/tradingsystem/orchestration/ticker_selection.py`:

```python
def build_cycle_ticker_list(
    alpaca_client: "AlpacaClientProtocol",
    candidate_universe: list[str],
    discovery_slots: int,
    lookback_days: int = 21,
) -> list[str]:
    held = {p.ticker for p in alpaca_client.get_position_details()}
    bars = alpaca_client.get_recent_daily_bars(candidate_universe, lookback_days)
    ranked = rank_candidates(bars)
    return select_tickers_for_cycle(held, ranked, discovery_slots)
```

Add `AlpacaClientProtocol` to the existing import line at the top of the file — replace:

```python
from tradingsystem.execution.alpaca_client import DailyBars
```

with:

```python
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol, DailyBars
```

And drop the quotes around the type hint in the new function signature (the import above makes them unnecessary):

```python
def build_cycle_ticker_list(
    alpaca_client: AlpacaClientProtocol,
    candidate_universe: list[str],
    discovery_slots: int,
    lookback_days: int = 21,
) -> list[str]:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ticker_selection.py -v`
Expected: PASS (11 tests total in this file)

- [ ] **Step 5: Wire into `cycle.py`**

In `src/tradingsystem/orchestration/cycle.py`, replace:

```python
from tradingsystem.config import RiskConfig, Settings, load_risk_config, load_watchlist
from tradingsystem.db.models import AgentRun, Decision, PortfolioSnapshot
from tradingsystem.db.repositories import get_active_breaker_event, record_breaker_trip
from tradingsystem.decision_engine.runner import run_research
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol
from tradingsystem.execution.executor import build_portfolio_state, place_order, sync_all_open_orders
from tradingsystem.orchestration import discord_alerts, heartbeat, memory_ingestion
from tradingsystem.risk.circuit_breaker import check_daily_breaker, check_weekly_breaker
from tradingsystem.risk.position_sizing import size_order
from tradingsystem.risk.stop_loss import is_stop_loss_triggered
from tradingsystem.risk.validation import OrderProposal
```

with:

```python
from tradingsystem.config import RiskConfig, Settings, load_candidate_universe, load_risk_config
from tradingsystem.db.models import AgentRun, Decision, PortfolioSnapshot
from tradingsystem.db.repositories import get_active_breaker_event, record_breaker_trip
from tradingsystem.decision_engine.runner import run_research
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol
from tradingsystem.execution.executor import build_portfolio_state, place_order, sync_all_open_orders
from tradingsystem.orchestration import discord_alerts, heartbeat, memory_ingestion, ticker_selection
from tradingsystem.risk.circuit_breaker import check_daily_breaker, check_weekly_breaker
from tradingsystem.risk.position_sizing import size_order
from tradingsystem.risk.stop_loss import is_stop_loss_triggered
from tradingsystem.risk.validation import OrderProposal
```

Replace the `run_full_cycle` signature and its first three lines:

```python
def run_full_cycle(
    session: Session,
    alpaca_client: AlpacaClientProtocol,
    run_type: str,
    settings: Settings | None = None,
    risk_config: RiskConfig | None = None,
    watchlist: list[str] | None = None,
) -> None:
    settings = settings or Settings()
    risk_config = risk_config or load_risk_config()
    watchlist = watchlist if watchlist is not None else load_watchlist().tickers

    heartbeat.record_heartbeat(session, run_type)
    session.commit()
```

with:

```python
def run_full_cycle(
    session: Session,
    alpaca_client: AlpacaClientProtocol,
    run_type: str,
    settings: Settings | None = None,
    risk_config: RiskConfig | None = None,
    watchlist: list[str] | None = None,
    candidate_universe: list[str] | None = None,
) -> None:
    settings = settings or Settings()
    risk_config = risk_config or load_risk_config()

    heartbeat.record_heartbeat(session, run_type)
    session.commit()
```

Then, immediately after the existing `memory_ingestion.ingest_trading_memory(session, settings)` / `session.commit()` block and before `ensure_snapshot_baseline(session, alpaca_client)`, insert the dynamic ticker-list computation — replace:

```python
    memory_ingestion.ingest_trading_memory(session, settings)
    session.commit()

    ensure_snapshot_baseline(session, alpaca_client)
    session.commit()
```

with:

```python
    memory_ingestion.ingest_trading_memory(session, settings)
    session.commit()

    if watchlist is None:
        universe = candidate_universe if candidate_universe is not None else load_candidate_universe().tickers
        watchlist = ticker_selection.build_cycle_ticker_list(
            alpaca_client, universe, settings.discovery_slots_per_cycle,
        )

    ensure_snapshot_baseline(session, alpaca_client)
    session.commit()
```

- [ ] **Step 6: Extend `FakeAlpacaClient` in `test_cycle.py` and run the full suite**

In `tests/test_cycle.py`, `FakeAlpacaClient` needs a `get_recent_daily_bars` method so that any test which doesn't already pass `watchlist=` explicitly can still run without crashing. Every existing test in this file already passes `watchlist=WATCHLIST` or `watchlist=["AAPL"]` explicitly, so this is a safety net, not something the existing tests will exercise — add it anyway for completeness and to support the new test in the next step. Add this method to the `FakeAlpacaClient` class (alongside the existing `get_position_details` method):

```python
    def get_recent_daily_bars(self, tickers, lookback_days):
        return {}
```

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all existing tests still PASS (no existing test's behavior changes — this step is additive).

- [ ] **Step 7: Add a `cycle.py`-level test for the dynamic ticker list**

Append to `tests/test_cycle.py`:

```python
def test_run_full_cycle_uses_dynamic_ticker_list_when_watchlist_not_given(db_session, monkeypatch):
    from tradingsystem.execution.alpaca_client import DailyBars, PositionDetail

    class DynamicFakeAlpacaClient(FakeAlpacaClient):
        def get_position_details(self):
            return [PositionDetail(ticker="AAPL", qty=5, avg_entry_price=100.0, current_price=100.0)]

        def get_recent_daily_bars(self, tickers, lookback_days):
            return {
                "MSFT": DailyBars(ticker="MSFT", closes=[100.0, 120.0], volumes=[100.0, 100.0]),
            }

    ScriptedGraph.calls = [
        (make_final_state("Hold: no clear edge"), "Hold"),
        (make_final_state("Hold: no clear edge"), "Hold"),
    ]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)
    client = DynamicFakeAlpacaClient(market_status="open")

    cycle.run_full_cycle(
        db_session, client, "pre_market", risk_config=RISK_CONFIG,
        candidate_universe=["MSFT"],
    )

    runs = db_session.query(AgentRun).all()
    assert {r.ticker for r in runs} == {"AAPL", "MSFT"}  # AAPL held (uncapped) + MSFT discovered
```

- [ ] **Step 8: Run the new test, then the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cycle.py -v`
Expected: PASS, including the new test.

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests PASS.

- [ ] **Step 9: Update `README.md`'s three watchlist references**

Replace:

```
  config.py                  Settings (.env) + risk_config.yaml / watchlist.yaml loaders
```

with:

```
  config.py                  Settings (.env) + risk_config.yaml / candidate_universe.yaml loaders
```

Replace:

```
config/
  risk_config.yaml              Risk numbers (position size, stop-loss, breakers) — edit freely
  watchlist.yaml                 Tickers — edit freely, takes effect next cycle
tests/                          pytest, runs against a dedicated trading_test database
```

with:

```
config/
  risk_config.yaml              Risk numbers (position size, stop-loss, breakers) — edit freely
  candidate_universe.yaml        Discovery-screen candidate pool — edit freely, takes effect next cycle
tests/                          pytest, runs against a dedicated trading_test database
```

Replace:

```
### `config/watchlist.yaml`

The ticker list. Add or remove freely; takes effect next cycle. You're
responsible for judgment calls on a new ticker's liquidity/coverage — the
system doesn't gate this automatically. Each additional ticker adds real
inference time per run, so watch total cycle time as the list grows.
```

with:

```
### `config/candidate_universe.yaml`

Every cycle, any ticker with a currently open position is always
reassessed (uncapped), plus a configurable number of "discovery" slots
(`DISCOVERY_SLOTS_PER_CYCLE`, default 4) filled from this file's pool via
a deterministic momentum + relative-volume screen — no LLM cost for the
screen itself, only for the tickers it actually selects. Add or remove
candidates freely; takes effect next cycle. You're responsible for
judgment calls on a candidate's liquidity/coverage — the system doesn't
gate this automatically. See
`docs/superpowers/specs/2026-08-25-ticker-selection-design.md` for the
full design.
```

- [ ] **Step 10: Commit**

```bash
git add src/tradingsystem/orchestration/ticker_selection.py src/tradingsystem/orchestration/cycle.py tests/test_ticker_selection.py tests/test_cycle.py README.md
git commit -m "Wire dynamic ticker selection into run_full_cycle"
```

---

## Final check

- [ ] Run the full suite once more: `.venv/Scripts/python.exe -m pytest -q` — expect all green, no warnings beyond the pre-existing `datetime.utcnow()` deprecation noise already present before this plan.
- [ ] Confirm `config/watchlist.yaml` no longer exists and `config/candidate_universe.yaml` does: `git status --porcelain config/`.
