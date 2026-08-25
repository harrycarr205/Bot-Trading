# Dynamic Ticker Selection — Design

## Goal

Replace the static, always-analyzed `config/watchlist.yaml` (currently 3
tickers, all researched every cycle) with a dynamic per-cycle ticker list
drawn from a much larger candidate universe, without letting LLM cost per
cycle scale with the size of that universe.

## Problem

TradingAgents' full pipeline (4 analysts + Research Manager + Trader +
Risk Judge + Portfolio Manager, split across Ollama Cloud's deep-think and
quick-think models) costs real LLM time/usage per ticker analyzed. Simply
growing the watchlist scales cost linearly. The system currently only ever
sees AAPL, MSFT, and SPY — no mechanism exists for broader market
coverage or for surfacing new opportunities outside that fixed list.

TradingAgents itself has no multi-ticker screening capability (confirmed
by reading the installed package) — any prioritization has to live in
this codebase, entirely upstream of TradingAgents.

## Approaches considered

1. **Fixed rotation** (user's original idea) — maintain a long list,
   analyze N per cycle, round-robin through the rest. Simple, but blind to
   relevance: with a large universe a given ticker might wait 1-2+ weeks
   for reanalysis regardless of whether anything actually happened to it.
2. **Deterministic screen → shortlist** — score the whole candidate
   universe each cycle on cheap, non-LLM signals (momentum, relative
   volume), analyze only the top N. Established real-world pattern
   (validated against a real "deterministic quant core + LLM overlay"
   project and standard quantitative-momentum screening methodology).
   Attention goes where warranted; cost stays flat. Doesn't by itself
   guarantee an open position gets reassessed if it isn't currently
   "moving."
3. **Hybrid (chosen)** — approach 2, plus every ticker with a currently
   open position is *always* included, uncapped, on top of a fixed
   discovery-slot budget for new candidates from the wider universe.

Approach 3 is what's specified below — confirmed with the user, including
the explicit choice that held-position reassessment is **not** capped by
the discovery budget (worst-case total tickers per cycle is bounded by how
many positions can physically be open at once, via `max_position_pct`/
`cash_reserve_pct`, not by the discovery-slot number).

## Design

### `config/candidate_universe.yaml` (renamed from `watchlist.yaml`)

Same shape as today's `watchlist.yaml` (`tickers: list[str]`), renamed
because its meaning changes: it's no longer "the list analyzed every
cycle," it's the pool the screen draws discovery candidates from. Starting
content — a sector-diverse set of liquid large/mid-caps plus a spread of
broad-market index ETFs (confirmed with the user):

```yaml
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

`config.py`: `WatchlistConfig`/`load_watchlist()` renamed to
`CandidateUniverseConfig`/`load_candidate_universe()`, pointed at the
renamed file. Same freely-editable, no-code-change-required philosophy as
today.

### New Settings field

```python
discovery_slots_per_cycle: int = 4
```

`.env`-configurable (`DISCOVERY_SLOTS_PER_CYCLE`), per the user's explicit
request to be able to tune this up/down based on observed API usage
without a code change.

### `AlpacaClient` — new capability

```python
@dataclasses.dataclass(frozen=True)
class DailyBars:
    ticker: str
    closes: list[float]   # oldest to newest
    volumes: list[float]  # oldest to newest, same ordering

def get_recent_daily_bars(self, tickers: list[str], lookback_days: int) -> dict[str, DailyBars]: ...
```

Added to both `AlpacaClientProtocol` and the real `AlpacaClient`, backed
by Alpaca's historical bars endpoint (batched across all requested
symbols in one call). This is historical daily OHLCV data, so it works
identically whether the market is currently open or closed — no
dependency on live quotes. A ticker Alpaca can't return data for (bad
symbol, delisted, etc.) is simply omitted from the result rather than
raising — the ranking step below treats a missing ticker as "not
rankable" and skips it, logged as a warning, rather than failing the
whole cycle.

**Bars always end at the most recently *completed* trading day, never
today's still-forming bar.** This system's cycles run at ~9:35am and
12:30pm ET — both well before a trading day finishes. Comparing a
partial in-progress day's volume against full historical daily averages
would make relative volume look artificially low at exactly the times
this actually runs, undermining the signal. `get_recent_daily_bars`
requests a date range ending at yesterday's (or, on a Monday, Friday's)
close, not "now."

### `orchestration/ticker_selection.py` (new)

Pure scoring/ranking (no I/O, fully unit-testable with plain dicts):

```python
def compute_momentum_pct(closes: list[float]) -> float:
    """% change from the oldest to the newest close in the window."""

def compute_relative_volume(volumes: list[float]) -> float:
    """Most recent day's volume / average of the preceding days."""

def rank_candidates(bars_by_ticker: dict[str, DailyBars]) -> list[str]:
    """Best-to-worst by composite rank: momentum rank + relative-volume
    rank, both computed cross-sectionally across the candidates present
    this cycle (not against any fixed absolute scale) — the same
    "rank each signal, sum the ranks" approach used in real quantitative
    momentum screening. Ties broken alphabetically for determinism."""

def select_tickers_for_cycle(
    held_tickers: set[str], ranked_candidates: list[str], discovery_slots: int
) -> list[str]:
    """held_tickers ∪ the top `discovery_slots` ranked candidates not
    already held. Held tickers are never capped by discovery_slots."""
```

`held_tickers` comes directly from live Alpaca position data
(`get_position_details()`), independent of `candidate_universe.yaml` — a
ticker you hold a position in is always reassessed even if it's since
been removed from the candidate universe file. The universe file only
governs *discovery* candidates, never which held positions get watched.

Composition function (impure — calls `AlpacaClient`):

```python
def build_cycle_ticker_list(
    alpaca_client: AlpacaClientProtocol,
    candidate_universe: list[str],
    discovery_slots: int,
    lookback_days: int = 21,
) -> list[str]:
    """held = {p.ticker for p in alpaca_client.get_position_details()}
    bars = alpaca_client.get_recent_daily_bars(candidate_universe, lookback_days)
    ranked = rank_candidates(bars)
    return select_tickers_for_cycle(held, ranked, discovery_slots)"""
```

Momentum uses the oldest-to-newest close across the fetched window;
relative volume compares the most recent day against the average of the
rest of the window. A single `lookback_days=21` fetch covers both — no
separate configurable windows for each signal (YAGNI; can split later if
the two signals need different horizons in practice).

### Wiring into `cycle.py`

`build_cycle_ticker_list` replaces the current
`watchlist = watchlist if watchlist is not None else load_watchlist().tickers`
line, called during the existing "housekeeping" step (alongside
`sync_all_open_orders`, `memory_ingestion.ingest_trading_memory`,
`ensure_snapshot_baseline`) — **before** the market-status check, not
after. Two reasons: `get_position_details()` doesn't depend on market
hours, and the market-closed journal-skip path needs *a* ticker list too.
Consequence worth being explicit about: on a market-closed day, the
journal now records held-positions + that day's discovery picks (computed
from real historical data) rather than a fixed static list — a more
accurate "what would have been researched" record, not a loss of
information.

`run_full_cycle`'s existing `watchlist: list[str] | None = None` parameter
keeps its name and role for tests (still an explicit override), but now
defaults via `build_cycle_ticker_list(...)` instead of
`load_watchlist().tickers` when not supplied.

### Testing

- Pure tests for `compute_momentum_pct`, `compute_relative_volume`,
  `rank_candidates` (including tie-breaking and a missing/unrankable
  ticker), `select_tickers_for_cycle` (held-uncapped behavior, discovery
  excluding already-held tickers, discovery_slots=0 edge case).
- `build_cycle_ticker_list` tested against a fake `AlpacaClient` (extends
  the existing `PositionDetail`/bars-returning fake pattern already used
  in `test_cycle.py`/`test_executor.py`).
- `cycle.py`-level test confirming the dynamic list is used for both the
  market-open research path and the market-closed journal path.

## Out of scope for this design

- Configurable momentum/volume lookback windows (fixed defaults for now).
- Any signal beyond momentum + relative volume (e.g. fundamentals-based
  screening) — can be added as a later, separate ranking signal without
  changing this design's shape.
- Automatic universe curation (adding/removing candidates based on
  liquidity checks) — the user remains responsible for curating
  `candidate_universe.yaml`, same as today's watchlist philosophy.
