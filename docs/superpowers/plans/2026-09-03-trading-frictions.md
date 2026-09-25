# Trading Frictions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every TradingAgents prompt a real transaction-cost/slippage estimate to weigh against expected edge, and add a hard average-daily-volume (ADV) ceiling to the deterministic risk layer — neither concept exists anywhere in the pipeline today.

**Architecture:** Two independent additions that share one new liquidity primitive:

1. **Risk-layer ADV check** — `execution/alpaca_client.py` gains a pure `compute_average_daily_dollar_volume(bars: DailyBars) -> float` helper (co-located with the `DailyBars` type it consumes, same pattern as that file's existing `round_to_tick` helper). `risk/validation.py` gains a new `check_liquidity(proposal, adv_notional, max_pct_of_adv) -> CheckResult`, buy-side-only (same pattern as `check_position_size`/`check_exposure`/`check_cash_reserve` — an exit, especially a stop-loss, must never be blocked by a liquidity check), added to `validate_order`'s check list. `orchestration/cycle.py` fetches the ADV bars only in the branch that already builds a non-`None` order proposal, and only for `place_order` calls that aren't stop-loss exits — stop-loss sells always pass `adv_notional=None`, which is harmless because `check_liquidity` short-circuits to pass on the sell side regardless of that value.
2. **Prompt-level friction context** — every one of TradingAgents' 13 prompt-building nodes (all four analysts, both researchers, the trader, all three risk debators, research manager, portfolio manager) interpolates `{instrument_context}`, sourced from `state["instrument_context"]`, which is set exactly once per run inside `TradingAgentsGraph._run_graph` by calling `self.resolve_instrument_context(ticker, asset_type)` (`.venv/Lib/site-packages/tradingagents/graph/trading_graph.py:336-346,424`). Rather than patching the pinned package, `decision_engine/runner.py` replaces that one **instance** attribute after constructing the graph — `graph.resolve_instrument_context = <wrapped closure>` — with a closure that calls the original bound method and appends a friction-cost sentence. Plain instance-attribute assignment shadows the class method for every subsequent `self.resolve_instrument_context(...)` call inside the pinned package, so no subclassing or monkeypatching of the package module is needed. The wrap is guarded with `hasattr(graph, "resolve_instrument_context")` so test doubles that don't implement it (see Global Constraints) are unaffected.

**Tech Stack:** Python 3.11, pytest. No changes to the pinned `tradingagents` package.

**Spec:** The Alpha Backlog (idea #03, "Put real trading frictions in front of every decision"), published artifact `https://claude.ai/code/artifact/58853951-b14c-4bfd-9d7e-97d14c43b033`. This plan document is self-contained; the artifact is provenance only.

## Global Constraints

- **Apply after `docs/superpowers/plans/2026-09-03-tool-usage-audit.md`.** Both plans edit `decision_engine/runner.py`'s attempt loop and both `ScriptedGraph` test doubles (`tests/test_decision_engine_runner.py` and `tests/test_cycle.py`). This plan's Task 4 steps are written against the state those files are in *after* that plan's Task 2 has landed (i.e. `ScriptedGraph.__init__` already accepts `callbacks=None`, and `runner.py`'s attempt loop already constructs `tool_audit = ToolCallAuditCallback()` and logs it). If that plan has not been applied, do it first — do not duplicate its edits here.
- Do not modify anything under `.venv/Lib/site-packages/tradingagents/` — pinned via `tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git@v0.3.1` (`pyproject.toml:26`).
- Alpaca equities trading is commission-free; the cost estimate this plan injects represents bid-ask spread + slippage only, as a conservative flat basis-point assumption — not a live quote-spread feed. Say so in the config comment and the prompt text itself so nobody mistakes it for a measured number later.
- `check_liquidity` must be buy-side-only, matching every other position-limiting check in `risk/validation.py` (`check_position_size`, `check_cash_reserve`, `check_exposure` all `return CheckResult(True)` immediately when `proposal.side != "buy"`). A stop-loss sell (`orchestration/cycle.py:152-161`) must always be able to fire regardless of liquidity data availability.
- `check_liquidity` fails closed when `adv_notional is None` (buy side only) — consistent with `risk/validation.py`'s own stated design: "fails closed (rejects) on any single failed check or ambiguous/malformed input."
- ADV is refetched separately by `cycle.py` (for the risk gate, after a decision) and by `runner.py` (for the prompt, before the decision). This is a deliberate simplification, not an oversight: the two call sites need the number at different points in the pipeline (one pre-decision, one post-decision), and re-fetching a 21-day daily-bars window twice per ticker per twice-daily cycle is negligible load on a free-tier-friendly API. Do not build a caching layer for this.

---

### Task 1: Config additions

**Files:**
- Modify: `src/tradingsystem/config.py` (add `estimated_round_trip_cost_bps` to `Settings`, `max_pct_of_adv` to `RiskConfig`)
- Modify: `config/risk_config.yaml`
- Modify: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings().estimated_round_trip_cost_bps: float` (default `10.0`); `RiskConfig.max_pct_of_adv: float` (loaded from `config/risk_config.yaml`, value `0.10`). Both are consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_estimated_round_trip_cost_bps_default():
    assert Settings(_env_file=None).estimated_round_trip_cost_bps == 10.0


def test_load_risk_config_includes_max_pct_of_adv():
    from tradingsystem.config import load_risk_config

    risk_config = load_risk_config()
    assert risk_config.max_pct_of_adv == 0.10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: both new tests FAIL (`estimated_round_trip_cost_bps` doesn't exist yet; `risk_config.yaml` has no `max_pct_of_adv` key, so `load_risk_config()` raises a pydantic validation error).

- [ ] **Step 3: Add the `Settings` field**

In `src/tradingsystem/config.py`, immediately after the `discovery_slots_per_cycle` field (line 78), add:

```python
    # Idea #03 (trading frictions): a conservative flat estimate of round-trip
    # transaction cost (spread + slippage) folded into every TradingAgents
    # prompt via decision_engine/friction_context.py, so the model has to
    # weigh it against expected edge. Alpaca equities are commission-free —
    # this is NOT a measured spread feed, just a deliberately cautious
    # constant. Revisit if it should vary by ticker/volatility later.
    estimated_round_trip_cost_bps: float = 10.0
```

- [ ] **Step 4: Add the `RiskConfig` field and the YAML value**

In `src/tradingsystem/config.py`, add to the `RiskConfig` model (after `stale_data_max_age_minutes`):

```python
    max_pct_of_adv: float
```

In `config/risk_config.yaml`, add after the `stale_data_max_age_minutes` line:

```yaml
max_pct_of_adv: 0.10          # max order notional as a % of the ticker's average daily dollar volume
```

- [ ] **Step 5: Update `.env.example` for documentation consistency**

`estimated_round_trip_cost_bps` is not read from a dedicated env var beyond pydantic-settings' automatic `ESTIMATED_ROUND_TRIP_COST_BPS` mapping (same as every other `Settings` field) — add a line documenting it near the other decision-engine settings in `.env.example`, after the `TRADINGAGENTS_MEMORY_LOG_PATH=` line:

```
# Idea #03: flat round-trip cost estimate (basis points) folded into every
# TradingAgents prompt. Not a live spread feed — a deliberately cautious constant.
ESTIMATED_ROUND_TRIP_COST_BPS=10.0
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/tradingsystem/config.py config/risk_config.yaml .env.example tests/test_config.py
git commit -m "feat: add trading-friction config (round-trip cost estimate, ADV cap)"
```

---

### Task 2: ADV computation and the `check_liquidity` risk check

**Files:**
- Modify: `src/tradingsystem/execution/alpaca_client.py` (add `compute_average_daily_dollar_volume`, after `DailyBars`)
- Modify: `src/tradingsystem/risk/validation.py` (add `check_liquidity`, wire into `validate_order`)
- Test: `tests/test_alpaca_client.py`, `tests/test_risk_validation.py`

**Interfaces:**
- Produces: `compute_average_daily_dollar_volume(bars: DailyBars) -> float`. `check_liquidity(proposal: OrderProposal, adv_notional: float | None, max_pct_of_adv: float) -> CheckResult`. `validate_order(...)` gains two new required parameters, `adv_notional: float | None` and `max_pct_of_adv: float`, and one new entry `("liquidity", check_liquidity(...))` in its checks list.
- Consumes: `DailyBars`, `OrderProposal`, `CheckResult` (all pre-existing).

- [ ] **Step 1: Write the failing tests for `compute_average_daily_dollar_volume`**

Add to `tests/test_alpaca_client.py`:

```python
from tradingsystem.execution.alpaca_client import DailyBars, compute_average_daily_dollar_volume


def test_compute_average_daily_dollar_volume():
    bars = DailyBars(ticker="AAPL", closes=[100.0, 110.0], volumes=[1_000_000.0, 2_000_000.0])
    # (100*1,000,000 + 110*2,000,000) / 2 = (100,000,000 + 220,000,000) / 2 = 160,000,000
    assert compute_average_daily_dollar_volume(bars) == 160_000_000.0


def test_compute_average_daily_dollar_volume_empty_bars_returns_zero():
    bars = DailyBars(ticker="AAPL", closes=[], volumes=[])
    assert compute_average_daily_dollar_volume(bars) == 0.0
```

(If `tests/test_alpaca_client.py` does not already import `DailyBars`, add it to the existing import line rather than duplicating the import statement.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_alpaca_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_average_daily_dollar_volume'`.

- [ ] **Step 3: Implement `compute_average_daily_dollar_volume`**

In `src/tradingsystem/execution/alpaca_client.py`, immediately after the `DailyBars` dataclass (after line 60), add:

```python
def compute_average_daily_dollar_volume(bars: DailyBars) -> float:
    """Average close*volume across the bars window — a liquidity proxy.

    Same window/data as ticker_selection.py's momentum/volume screen; this is
    the risk layer's independent read of it, not a shared cache (see the
    trading-frictions plan's Global Constraints on why it's refetched).
    """
    if not bars.closes or not bars.volumes:
        return 0.0
    dollar_volumes = [c * v for c, v in zip(bars.closes, bars.volumes)]
    return sum(dollar_volumes) / len(dollar_volumes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_alpaca_client.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing tests for `check_liquidity` and `validate_order`**

Add to `tests/test_risk_validation.py` (extend the existing import line with `check_liquidity`):

```python
def test_liquidity_within_cap_passes():
    proposal = make_proposal(qty=10, limit_price=100.0)  # $1,000 order
    assert check_liquidity(proposal, adv_notional=100_000.0, max_pct_of_adv=0.10).passed  # 1% of ADV


def test_liquidity_over_cap_fails():
    proposal = make_proposal(qty=200, limit_price=100.0)  # $20,000 order
    assert not check_liquidity(proposal, adv_notional=100_000.0, max_pct_of_adv=0.10).passed  # 20% of ADV


def test_liquidity_missing_adv_fails_closed():
    proposal = make_proposal()
    assert not check_liquidity(proposal, adv_notional=None, max_pct_of_adv=0.10).passed


def test_liquidity_zero_adv_fails_closed():
    proposal = make_proposal()
    assert not check_liquidity(proposal, adv_notional=0.0, max_pct_of_adv=0.10).passed


def test_liquidity_sell_side_always_passes_even_with_no_adv_data():
    # A stop-loss exit must never be blocked by a liquidity check.
    proposal = make_proposal(side="sell", qty=1000, limit_price=100.0)
    assert check_liquidity(proposal, adv_notional=None, max_pct_of_adv=0.10).passed


def test_validate_order_now_requires_liquidity_check_to_pass():
    proposal = make_proposal(qty=200, limit_price=100.0)  # 20% of the ADV below
    portfolio = make_portfolio()
    result = validate_order(
        proposal, portfolio, market_status="open", now=NOW,
        max_position_pct=0.50, cash_reserve_pct=0.20, stale_data_max_age_minutes=15,
        adv_notional=100_000.0, max_pct_of_adv=0.10,
    )
    assert not result.approved
    assert "liquidity" in result.rejection_reasons
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_risk_validation.py -v`
Expected: FAIL — `check_liquidity` doesn't exist, and `validate_order(...)` doesn't accept `adv_notional`/`max_pct_of_adv` yet (also breaks the pre-existing `test_validate_order_*` calls once `validate_order`'s signature changes in Step 7 — that's expected and fixed in Step 8).

- [ ] **Step 7: Implement `check_liquidity` and wire it into `validate_order`**

In `src/tradingsystem/risk/validation.py`, add after `check_duplicate_order` (after line 126):

```python
def check_liquidity(
    proposal: OrderProposal, adv_notional: float | None, max_pct_of_adv: float
) -> CheckResult:
    """Buy-side only, same pattern as check_position_size/check_exposure — an
    exit (especially a stop-loss) must never be blocked by a liquidity check.
    Fails closed if ADV data is unavailable or non-positive, consistent with
    this module's stance on ambiguous input.
    """
    if proposal.side != "buy":
        return CheckResult(True)
    if adv_notional is None or adv_notional <= 0:
        return CheckResult(False, "average daily dollar volume is unavailable or non-positive")
    order_notional = proposal.qty * proposal.limit_price
    resulting_pct_of_adv = order_notional / adv_notional
    if resulting_pct_of_adv > max_pct_of_adv:
        return CheckResult(
            False,
            f"order notional is {resulting_pct_of_adv:.2%} of average daily dollar volume, "
            f"exceeds max_pct_of_adv {max_pct_of_adv:.2%}",
        )
    return CheckResult(True)
```

Replace `validate_order` (lines 129-149) with:

```python
def validate_order(
    proposal: OrderProposal,
    portfolio: PortfolioState,
    market_status: str,
    now: datetime.datetime,
    max_position_pct: float,
    cash_reserve_pct: float,
    stale_data_max_age_minutes: int,
    adv_notional: float | None,
    max_pct_of_adv: float,
) -> OrderValidationResult:
    """Aggregate all checks. Approved only if every single check passes — fails closed."""
    checks = [
        ("market_status", check_market_status(market_status)),
        ("order_type", check_order_type(proposal.order_type)),
        ("position_size", check_position_size(proposal, portfolio, max_position_pct)),
        ("cash_reserve", check_cash_reserve(proposal, portfolio, cash_reserve_pct)),
        ("exposure", check_exposure(proposal, portfolio, cash_reserve_pct)),
        ("liquidity", check_liquidity(proposal, adv_notional, max_pct_of_adv)),
        ("stale_data", check_stale_data(proposal, now, stale_data_max_age_minutes)),
        ("duplicate_order", check_duplicate_order(proposal, portfolio)),
    ]
    approved = all(result.passed for _, result in checks)
    return OrderValidationResult(approved=approved, checks=checks)
```

- [ ] **Step 8: Fix the pre-existing `validate_order` tests for the new required parameters**

The three existing calls to `validate_order(...)` in `tests/test_risk_validation.py` (`test_validate_order_all_pass_is_approved`, `test_validate_order_single_failure_fails_closed`, `test_validate_order_market_closed_fails_closed`) now need `adv_notional=1_000_000.0, max_pct_of_adv=0.10` added to their keyword arguments (a comfortably large ADV so the new liquidity check doesn't change what those tests are actually verifying). For each of the three, add the two new kwargs after `stale_data_max_age_minutes=15,`:

```python
        stale_data_max_age_minutes=15,
        adv_notional=1_000_000.0,
        max_pct_of_adv=0.10,
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/test_risk_validation.py -v`
Expected: PASS (all tests, old and new).

- [ ] **Step 10: Commit**

```bash
git add src/tradingsystem/execution/alpaca_client.py src/tradingsystem/risk/validation.py tests/test_alpaca_client.py tests/test_risk_validation.py
git commit -m "feat: add an ADV-based liquidity check to the deterministic risk layer"
```

---

### Task 3: Wire the liquidity check into `place_order` and `orchestration/cycle.py`

**Files:**
- Modify: `src/tradingsystem/execution/executor.py` (`place_order`)
- Modify: `src/tradingsystem/orchestration/cycle.py` (`run_full_cycle`'s per-ticker loop, `check_and_execute_stop_losses`)
- Test: `tests/test_executor.py`, `tests/test_cycle.py`

**Interfaces:**
- Consumes: `check_liquidity`/`validate_order`'s new parameters from Task 2.
- Produces: `place_order(...)` gains two new required parameters, `adv_notional: float | None` and `max_pct_of_adv: float`, positioned after `stale_data_max_age_minutes` and before `now`.

- [ ] **Step 1: Update `place_order`'s signature and its call to `validate_order`**

In `src/tradingsystem/execution/executor.py`, replace the `place_order` signature (lines 50-60):

```python
def place_order(
    session: Session,
    client: AlpacaClientProtocol,
    proposal: OrderProposal,
    decision_id: uuid.UUID,
    kill_switch_file: str,
    max_position_pct: float,
    cash_reserve_pct: float,
    stale_data_max_age_minutes: int,
    adv_notional: float | None,
    max_pct_of_adv: float,
    now: datetime.datetime | None = None,
) -> ExecutionResult:
```

And its `validate_order(...)` call (lines 68-76):

```python
    result = validate_order(
        proposal,
        portfolio,
        market_status=market_status,
        now=now or datetime.datetime.utcnow(),
        max_position_pct=max_position_pct,
        cash_reserve_pct=cash_reserve_pct,
        stale_data_max_age_minutes=stale_data_max_age_minutes,
        adv_notional=adv_notional,
        max_pct_of_adv=max_pct_of_adv,
    )
```

- [ ] **Step 2: Update `tests/test_executor.py`'s `COMMON_KWARGS`**

`COMMON_KWARGS` (line 97-101) is used by every `place_order(...)` call in this file via `**COMMON_KWARGS`. Replace it with:

```python
COMMON_KWARGS = dict(
    max_position_pct=0.10,
    cash_reserve_pct=0.20,
    stale_data_max_age_minutes=15,
    adv_notional=1_000_000.0,
    max_pct_of_adv=0.10,
)
```

(Every existing test proposal in this file is $100-$1,000 notional, well under 10% of a $1,000,000 ADV — this keeps every existing assertion's outcome unchanged.)

- [ ] **Step 3: Run `test_executor.py` to verify it still passes**

Run: `pytest tests/test_executor.py -v`
Expected: PASS — this step should require no other changes, since every call site uses `**COMMON_KWARGS`.

- [ ] **Step 4: Wire ADV fetch + threading into `orchestration/cycle.py`**

In `src/tradingsystem/orchestration/cycle.py`, add the new import:

```python
from tradingsystem.execution.alpaca_client import compute_average_daily_dollar_volume
```

(add it to the existing `from tradingsystem.execution.alpaca_client import AlpacaClientProtocol` line rather than a separate line: `from tradingsystem.execution.alpaca_client import AlpacaClientProtocol, compute_average_daily_dollar_volume`)

In `check_and_execute_stop_losses`, the `place_order(...)` call (lines 156-161) always represents a sell — pass `adv_notional=None` (harmless, `check_liquidity` short-circuits on non-buy) without fetching bars, so the stop-loss path never depends on liquidity data being available:

```python
        exec_result = place_order(
            session, alpaca_client, proposal, decision.id,
            settings.kill_switch_file, risk_config.max_position_pct,
            risk_config.cash_reserve_pct, risk_config.stale_data_max_age_minutes,
            adv_notional=None, max_pct_of_adv=risk_config.max_pct_of_adv,
            now=now,
        )
```

In `run_full_cycle`'s per-ticker loop, the `if proposal is not None:` block (lines 260-280) fetches ADV once, right before calling `place_order`:

```python
            if proposal is not None:
                adv_bars = alpaca_client.get_recent_daily_bars([ticker], lookback_days=21)
                adv_notional = (
                    compute_average_daily_dollar_volume(adv_bars[ticker]) if ticker in adv_bars else None
                )
                exec_result = place_order(
                    session, alpaca_client, proposal, result.decision_id,
                    settings.kill_switch_file, risk_config.max_position_pct,
                    risk_config.cash_reserve_pct, risk_config.stale_data_max_age_minutes,
                    adv_notional=adv_notional, max_pct_of_adv=risk_config.max_pct_of_adv,
                    now=datetime.datetime.utcnow(),
                )
```

(Only the two new lines building `adv_bars`/`adv_notional` and the two new kwargs on `place_order` are added — the rest of that block, including the `if exec_result.submitted:` alerting logic below it, is unchanged.)

- [ ] **Step 5: Update `tests/test_cycle.py`'s `FakeAlpacaClient` to serve bars data**

`FakeAlpacaClient.get_recent_daily_bars` (line 39-40) currently always returns `{}`. Give it an optional `bars` constructor parameter, same pattern as `tests/test_ticker_selection.py`'s `FakeAlpacaClientForSelection`:

Replace the `__init__` signature (lines 13-16):

```python
    def __init__(self, equity=100_000.0, cash=80_000.0, positions=None, open_orders=None,
                 market_status="open", price=100.0, price_overrides=None,
                 raise_on_price_for=None, submit_response=None, order_statuses=None,
                 position_details=None, bars=None):
```

and add, alongside the other `self.x = x or ...` lines in `__init__` (after `self.position_details = position_details or []`):

```python
        self.bars = bars or {}
```

Replace `get_recent_daily_bars` (lines 39-40):

```python
    def get_recent_daily_bars(self, tickers, lookback_days):
        return {t: self.bars[t] for t in tickers if t in self.bars}
```

- [ ] **Step 6: Give the order-placing tests enough ADV to clear the new check**

Two existing tests place a buy order and will now fail the liquidity check because the default fake has no bars for the ticker being bought (`adv_notional=None` fails closed on the buy side). Add a `bars=` argument to each client construction with a comfortably liquid fake ticker — $100 close x 1,000,000 volume = $100,000,000 ADV, far above the $10,000 orders these tests place.

In `test_buy_decision_sizes_and_places_order` (around line 200), change:

```python
    client = FakeAlpacaClient(market_status="open", equity=100_000.0, price=100.0,
                               submit_response=SubmittedOrder(alpaca_order_id="abc123", status="new"))
```

to:

```python
    from tradingsystem.execution.alpaca_client import DailyBars
    client = FakeAlpacaClient(market_status="open", equity=100_000.0, price=100.0,
                               submit_response=SubmittedOrder(alpaca_order_id="abc123", status="new"),
                               bars={"AAPL": DailyBars(ticker="AAPL", closes=[100.0], volumes=[1_000_000.0])})
```

In `test_one_ticker_exception_does_not_stop_the_others` (around line 220), change:

```python
    client = FakeAlpacaClient(market_status="open", equity=100_000.0, price=100.0,
                               raise_on_price_for={"AAPL"},
                               submit_response=SubmittedOrder(alpaca_order_id="xyz789", status="new"))
```

to:

```python
    from tradingsystem.execution.alpaca_client import DailyBars
    client = FakeAlpacaClient(market_status="open", equity=100_000.0, price=100.0,
                               raise_on_price_for={"AAPL"},
                               submit_response=SubmittedOrder(alpaca_order_id="xyz789", status="new"),
                               bars={"MSFT": DailyBars(ticker="MSFT", closes=[100.0], volumes=[1_000_000.0])})
```

(This test's order that actually goes through is MSFT, not AAPL — AAPL raises on `get_latest_price` before sizing ever happens, so only MSFT needs ADV data.)

- [ ] **Step 7: Run `test_cycle.py` to verify it passes**

Run: `pytest tests/test_cycle.py -v`
Expected: PASS — every other test in this file either never reaches a non-`None` proposal (Hold decisions, market-closed, breaker-blocked) or is a stop-loss sell (unaffected by the liquidity check per Task 2's design), so none of them needed bars data added.

- [ ] **Step 8: Run the full test suite to check for regressions**

Run: `pytest -v`
Expected: no new failures.

- [ ] **Step 9: Commit**

```bash
git add src/tradingsystem/execution/executor.py src/tradingsystem/orchestration/cycle.py tests/test_executor.py tests/test_cycle.py
git commit -m "feat: enforce the ADV liquidity cap on every non-exit order"
```

---

### Task 4: Friction context in every prompt

**Files:**
- Create: `src/tradingsystem/decision_engine/friction_context.py`
- Modify: `src/tradingsystem/decision_engine/runner.py`
- Modify: `src/tradingsystem/orchestration/cycle.py` (the `run_research(...)` call site)
- Modify: `tests/test_decision_engine_runner.py`
- Test: `tests/test_friction_context.py`, `tests/test_decision_engine_runner.py`

**Interfaces:**
- Produces: `build_friction_note(round_trip_cost_bps: float, adv_notional: float | None, max_pct_of_adv: float) -> str`; `wrap_resolve_instrument_context(original: Callable[[str, str], str], friction_note: str) -> Callable[[str, str], str]`.
- `run_research(...)`'s signature gains two new required parameters: `alpaca_client: AlpacaClientProtocol` and `risk_config: RiskConfig`, inserted after `market_status` and before `run_type`.

**Depends on:** `docs/superpowers/plans/2026-09-03-tool-usage-audit.md` Task 2 already applied (see Global Constraints) — the edits below assume `runner.py`'s attempt loop already contains `tool_audit = ToolCallAuditCallback()` and the `log.info("tool calls requested...")` line, and that both `ScriptedGraph` test doubles already accept `callbacks=None`.

- [ ] **Step 1: Write the failing tests for `friction_context.py`**

Create `tests/test_friction_context.py`:

```python
from tradingsystem.decision_engine.friction_context import (
    build_friction_note,
    wrap_resolve_instrument_context,
)


def test_build_friction_note_includes_cost_bps():
    note = build_friction_note(round_trip_cost_bps=10.0, adv_notional=1_000_000.0, max_pct_of_adv=0.10)
    assert "10" in note
    assert "basis points" in note


def test_build_friction_note_includes_adv_threshold():
    note = build_friction_note(round_trip_cost_bps=10.0, adv_notional=1_000_000.0, max_pct_of_adv=0.10)
    assert "1,000,000" in note
    assert "10%" in note


def test_build_friction_note_handles_missing_adv_gracefully():
    note = build_friction_note(round_trip_cost_bps=10.0, adv_notional=None, max_pct_of_adv=0.10)
    assert "unavailable" in note
    assert "10" in note  # the cost sentence is still present


def test_wrap_resolve_instrument_context_appends_friction_note():
    def original(ticker, asset_type="stock"):
        return f"The instrument to analyze is `{ticker}`."

    wrapped = wrap_resolve_instrument_context(original, "FRICTION NOTE HERE")
    result = wrapped("AAPL", "stock")
    assert result.startswith("The instrument to analyze is `AAPL`.")
    assert result.endswith("FRICTION NOTE HERE")


def test_wrap_resolve_instrument_context_defaults_asset_type():
    def original(ticker, asset_type="stock"):
        return f"{ticker}/{asset_type}"

    wrapped = wrap_resolve_instrument_context(original, "note")
    assert wrapped("AAPL") == "AAPL/stock note"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_friction_context.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `friction_context.py`**

Create `src/tradingsystem/decision_engine/friction_context.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_friction_context.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/decision_engine/friction_context.py tests/test_friction_context.py
git commit -m "feat: add friction-note builder for TradingAgents prompts"
```

---

- [ ] **Step 6: Write the failing test for `run_research()`'s new parameters**

Add to `tests/test_decision_engine_runner.py` a minimal fake (near the top, after `ScriptedGraph`):

```python
class FakeAlpacaClientForResearch:
    """Only what run_research() needs for the ADV-fetch side of the friction note."""

    def __init__(self, bars=None):
        self.bars = bars or {}

    def get_recent_daily_bars(self, tickers, lookback_days):
        return {t: self.bars[t] for t in tickers if t in self.bars}
```

Add near the top of the file, alongside any other module-level constants:

```python
from tradingsystem.config import RiskConfig

RISK_CONFIG = RiskConfig(
    max_position_pct=0.10,
    cash_reserve_pct=0.20,
    stop_loss_pct=0.08,
    daily_drawdown_breaker_pct=0.03,
    weekly_drawdown_breaker_pct=0.08,
    stale_data_max_age_minutes=15,
    max_pct_of_adv=0.10,
)
```

Update every existing `run_research(...)` call in this file (there are 4 pre-existing plus the 1 added by the tool-usage-audit plan, all sharing this same call shape) to add `alpaca_client=FakeAlpacaClientForResearch(), risk_config=RISK_CONFIG,` after `market_status="open",`. For example, `test_successful_run_persists_agent_run_decision_and_transcript`'s call:

```python
    result = run_research(
        db_session, "AAPL", "2026-01-05", market_status="open",
        alpaca_client=FakeAlpacaClientForResearch(), risk_config=RISK_CONFIG,
        settings=settings,
    )
```

Apply the same `alpaca_client=FakeAlpacaClientForResearch(), risk_config=RISK_CONFIG,` insertion to the other `run_research(...)` calls in this file.

Then add a new test verifying the wrapping actually happens, using a fake graph that *does* implement `resolve_instrument_context` (unlike `ScriptedGraph`):

```python
class ScriptedGraphWithInstrumentContext(ScriptedGraph):
    """Same scripted behavior as ScriptedGraph, but with a real
    resolve_instrument_context method so the friction-wrap can be observed."""

    def resolve_instrument_context(self, ticker, asset_type="stock"):
        return f"The instrument to analyze is `{ticker}`."


def test_friction_note_wraps_resolve_instrument_context_when_supported(db_session, monkeypatch):
    constructed_instances = []

    class CapturingGraph(ScriptedGraphWithInstrumentContext):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            constructed_instances.append(self)

    CapturingGraph.calls = [(make_final_state(), "Buy")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", CapturingGraph)

    settings = Settings(tradingagents_run_max_attempts=1, estimated_round_trip_cost_bps=10.0)
    result = run_research(
        db_session, "AAPL", "2026-01-05", market_status="open",
        alpaca_client=FakeAlpacaClientForResearch(), risk_config=RISK_CONFIG,
        settings=settings,
    )

    assert result.ok
    assert len(constructed_instances) == 1
    wrapped_context = constructed_instances[0].resolve_instrument_context("AAPL")
    assert wrapped_context.startswith("The instrument to analyze is `AAPL`.")
    assert "basis points" in wrapped_context


def test_scripted_graph_without_instrument_context_is_unaffected(db_session, monkeypatch):
    # ScriptedGraph (used by every other test in this file) has no
    # resolve_instrument_context at all — run_research() must not raise.
    ScriptedGraph.calls = [(make_final_state(), "Buy")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)

    result = run_research(
        db_session, "AAPL", "2026-01-05", market_status="open",
        alpaca_client=FakeAlpacaClientForResearch(), risk_config=RISK_CONFIG,
        settings=Settings(tradingagents_run_max_attempts=1),
    )
    assert result.ok
```

- [ ] **Step 7: Run tests to verify the new ones fail and existing ones error on the missing parameters**

Run: `pytest tests/test_decision_engine_runner.py -v`
Expected: `TypeError: run_research() missing 2 required positional arguments: 'alpaca_client' and 'risk_config'` on every test — expected until Step 8 lands.

- [ ] **Step 8: Wire the new parameters and the wrap into `run_research()`**

In `src/tradingsystem/decision_engine/runner.py`, add imports:

```python
from tradingsystem.config import RiskConfig, Settings
from tradingsystem.decision_engine.friction_context import build_friction_note, wrap_resolve_instrument_context
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol, compute_average_daily_dollar_volume
```

(`Settings` is already imported — extend that existing line rather than duplicating it; same for adding `RiskConfig` to whatever already imports from `tradingsystem.config`.)

Change the `run_research` signature:

```python
def run_research(
    session: Session,
    ticker: str,
    trade_date: str,
    market_status: str,
    alpaca_client: AlpacaClientProtocol,
    risk_config: RiskConfig,
    run_type: str = "pre_market",
    settings: Settings | None = None,
) -> ResearchResult:
```

Immediately after `config = build_ta_config(settings)` and before the `for attempt in range(...)` loop, compute the friction note once (it doesn't vary by attempt):

```python
    adv_bars = alpaca_client.get_recent_daily_bars([ticker], lookback_days=21)
    adv_notional = compute_average_daily_dollar_volume(adv_bars[ticker]) if ticker in adv_bars else None
    friction_note = build_friction_note(
        round_trip_cost_bps=settings.estimated_round_trip_cost_bps,
        adv_notional=adv_notional,
        max_pct_of_adv=risk_config.max_pct_of_adv,
    )
```

Inside the attempt loop, immediately after constructing `graph` and before calling `graph.propagate(...)`, add the wrap (this sits right after the `graph = TradingAgentsGraph(...)` line the tool-usage-audit plan already added):

```python
            graph = TradingAgentsGraph(debug=False, config=config, callbacks=[tool_audit])
            if hasattr(graph, "resolve_instrument_context"):
                # Test doubles (ScriptedGraph) don't implement this method —
                # only the real TradingAgentsGraph and fakes that opt in do.
                graph.resolve_instrument_context = wrap_resolve_instrument_context(
                    graph.resolve_instrument_context, friction_note,
                )
            final_state, rating = graph.propagate(ticker, trade_date)
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/test_decision_engine_runner.py -v`
Expected: PASS.

- [ ] **Step 10: Update `orchestration/cycle.py`'s call site**

In `src/tradingsystem/orchestration/cycle.py`, the `run_research(...)` call inside `run_full_cycle`'s per-ticker loop (lines 243-246) already has both `alpaca_client` and `risk_config` in scope as local variables. Change it to:

```python
            result = run_research(
                session, ticker, trade_date=_ny_today().isoformat(),
                market_status=market_status, alpaca_client=alpaca_client, risk_config=risk_config,
                run_type=run_type, settings=settings,
            )
```

- [ ] **Step 11: Run the full test suite to check for regressions**

Run: `pytest -v`
Expected: no new failures. `test_cycle.py`'s tests don't call `run_research` directly (they call `run_full_cycle`, which now forwards `alpaca_client`/`risk_config` internally) — no test_cycle.py edits are needed for this step, since its `FakeAlpacaClient` already implements `get_recent_daily_bars` (Task 3, Step 5) and returns `{}` for any ticker it wasn't given bars for, which `run_research()`'s new ADV-fetch handles gracefully (`adv_notional=None` only affects prompt text here, not a hard gate).

- [ ] **Step 12: Commit**

```bash
git add src/tradingsystem/decision_engine/runner.py src/tradingsystem/orchestration/cycle.py tests/test_decision_engine_runner.py
git commit -m "feat: inject trading-friction context into every TradingAgents prompt"
```

---

## Self-Review

**1. Spec coverage:** Idea #03 asks for (a) real cost numbers in front of every analyst/trader prompt and (b) an ADV-based illiquidity check in the risk layer alongside the existing position-size cap. Task 4 covers (a); Tasks 2-3 cover (b). The backlog's explicit note that the existing 10%-of-equity cap should stay the ceiling either way is preserved — `check_liquidity` is additive to `validate_order`'s check list, not a replacement for `check_position_size`.

**2. Placeholder scan:** No TBDs. Every code block is complete and runnable as written.

**3. Type consistency:** `check_liquidity(proposal, adv_notional, max_pct_of_adv) -> CheckResult` (Task 2) matches its call inside `validate_order` (same task) and its threading through `place_order` (Task 3) and `run_research`'s parallel ADV fetch (Task 4) — same `adv_notional: float | None` type throughout. `build_friction_note`/`wrap_resolve_instrument_context` (Task 4) signatures match their test usage and their call sites in `runner.py`.

**Cross-plan dependency:** This plan explicitly requires `2026-09-03-tool-usage-audit.md` to be applied first (both plans edit `runner.py`'s attempt loop and both `ScriptedGraph` test doubles). Flagged in Global Constraints and again at Task 4.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-03-trading-frictions.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
