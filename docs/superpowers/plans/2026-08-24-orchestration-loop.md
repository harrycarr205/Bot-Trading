# Orchestration Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the standalone orchestration process that composes the existing, individually-tested decision-engine/risk/execution modules into the twice-daily research → validate → execute → journal cycle described in ARCHITECTURE.md §2/§6.

**Architecture:** A framework-free `run_full_cycle()` function in `orchestration/cycle.py` does all the real work (market-status gate → circuit-breaker gate → per-ticker research/size/execute loop), so it's testable without APScheduler or real Ollama/Alpaca. A thin `orchestration/scheduler.py` wires that function into two daily cron jobs. Small supporting modules (`heartbeat.py`, `discord_alerts.py`) are built first since `cycle.py` depends on both.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (existing `db_session` fixture / Postgres), APScheduler (new dependency), httpx (already a transitive dependency) for Discord webhooks, pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-orchestration-loop-design.md`

## Global Constraints

- No migrations tool — schema changes go through `Base.metadata.create_all()` (see `db/init_db.py`), same as every existing table.
- Config is not hardcoded — new tunables (`pre_market_cron`, `midday_cron`) are `Settings` fields, overridable via `.env`.
- Paper trading only — nothing in this plan touches `trading_mode` or constructs a live `AlpacaClient`.
- `executor.place_order()` already re-checks the kill switch and re-derives portfolio state before every order — do not add a duplicate kill-switch check in `cycle.py`.
- One ticker's unhandled exception must never stop the rest of the watchlist for that cycle.
- `discord_alerts.send_alert()` must no-op (log only) when `settings.discord_webhook_url` is empty — dev/test runs without a configured webhook must not crash.
- All cron/date logic uses `America/New_York`, regardless of host machine locale — the scheduling intent is tied to US market hours.
- `PortfolioSnapshot.equity`/`.cash` are `Numeric` columns — reading them back from the DB yields `decimal.Decimal`, not `float`. Always wrap with `float(...)` before arithmetic against a plain float (e.g. before calling `check_daily_breaker`/`check_weekly_breaker`), or a `TypeError` (`decimal.Decimal` vs `float`) will surface only when a real DB row is read back, not in isolated unit tests that skip the DB.

---

### Task 1: Add `decision_id` to `ResearchResult`

`executor.place_order()` needs the persisted `Decision` row's own primary key as its `decision_id` FK parameter, but `run_research()` currently discards it after constructing the `Decision`. This is the one upstream change the rest of the plan depends on.

**Files:**
- Modify: `src/tradingsystem/decision_engine/runner.py:39-45` (dataclass), `src/tradingsystem/decision_engine/runner.py:115-130` (success path)
- Test: `tests/test_decision_engine_runner.py:49-72` (extend the existing success test)

**Interfaces:**
- Produces: `ResearchResult.decision_id: uuid.UUID | None` — `None` only when `ok=False`; set to the flushed `Decision.id` on success.

- [ ] **Step 1: Write the failing assertion**

Add to the end of `test_successful_run_persists_agent_run_decision_and_transcript` in `tests/test_decision_engine_runner.py`:

```python
    assert result.decision_id == decision.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_decision_engine_runner.py::test_successful_run_persists_agent_run_decision_and_transcript -v`
Expected: FAIL with `AttributeError: 'ResearchResult' object has no attribute 'decision_id'`

- [ ] **Step 3: Add the field and populate it**

In `src/tradingsystem/decision_engine/runner.py`, change the dataclass:

```python
@dataclasses.dataclass(frozen=True)
class ResearchResult:
    ok: bool
    agent_run_id: uuid.UUID
    decision_id: uuid.UUID | None = None
    rating: str | None = None
    decision: str | None = None
    reasoning_summary: str | None = None
```

And in the success branch of `run_research` (where `decision` is constructed and flushed), update the return statement:

```python
        return ResearchResult(
            ok=True,
            agent_run_id=agent_run.id,
            decision_id=decision.id,
            rating=rating,
            decision=decision.decision,
            reasoning_summary=decision.reasoning_summary,
        )
```

- [ ] **Step 4: Run the full test file to verify it passes and nothing else broke**

Run: `.venv\Scripts\python.exe -m pytest tests/test_decision_engine_runner.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/decision_engine/runner.py tests/test_decision_engine_runner.py
git commit -m "Add decision_id to ResearchResult for executor wiring"
```

---

### Task 2: `SchedulerHeartbeat` model + `heartbeat.py`

**Files:**
- Modify: `src/tradingsystem/db/models.py` (add model class)
- Create: `src/tradingsystem/orchestration/__init__.py` (empty)
- Create: `src/tradingsystem/orchestration/heartbeat.py`
- Test: `tests/test_heartbeat.py`

**Interfaces:**
- Produces: `record_heartbeat(session: Session, run_type: str, ticker: str | None = None) -> SchedulerHeartbeat`, `get_heartbeat(session: Session) -> SchedulerHeartbeat | None`
- Consumes: `tradingsystem.db.models.SchedulerHeartbeat` (new), `tests/conftest.py`'s `db_session` fixture (existing)

- [ ] **Step 1: Write the failing test**

Create `tests/test_heartbeat.py`:

```python
import datetime

from tradingsystem.orchestration.heartbeat import get_heartbeat, record_heartbeat


def test_get_heartbeat_returns_none_when_never_recorded(db_session):
    assert get_heartbeat(db_session) is None


def test_record_heartbeat_creates_row(db_session):
    before = datetime.datetime.utcnow()
    row = record_heartbeat(db_session, "pre_market", ticker="AAPL")

    assert row.component == "scheduler"
    assert row.last_run_type == "pre_market"
    assert row.last_ticker == "AAPL"
    assert row.last_seen_at >= before

    fetched = get_heartbeat(db_session)
    assert fetched is not None
    assert fetched.last_ticker == "AAPL"


def test_record_heartbeat_upserts_same_row(db_session):
    first = record_heartbeat(db_session, "pre_market", ticker="AAPL")
    second = record_heartbeat(db_session, "midday", ticker="MSFT")

    assert first.component == second.component == "scheduler"
    assert get_heartbeat(db_session).last_run_type == "midday"
    assert get_heartbeat(db_session).last_ticker == "MSFT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_heartbeat.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradingsystem.orchestration'`

- [ ] **Step 3: Add the `SchedulerHeartbeat` model**

In `src/tradingsystem/db/models.py`, add at the end of the file:

```python
class SchedulerHeartbeat(Base):
    __tablename__ = "scheduler_heartbeats"

    component: Mapped[str] = mapped_column(primary_key=True)
    last_seen_at: Mapped[datetime.datetime]
    last_run_type: Mapped[str | None]
    last_ticker: Mapped[str | None]
```

- [ ] **Step 4: Create the orchestration package and heartbeat module**

Create `src/tradingsystem/orchestration/__init__.py` (empty file).

Create `src/tradingsystem/orchestration/heartbeat.py`:

```python
"""Single-row liveness marker for the orchestration scheduler — ARCHITECTURE.md §6/§7.

Updated every cycle regardless of trade activity, so a silent crash is distinguishable
from a legitimate no-trade day. A future dashboard/monitor reads staleness from this;
this module only writes it.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from tradingsystem.db.models import SchedulerHeartbeat

_COMPONENT = "scheduler"


def record_heartbeat(session: Session, run_type: str, ticker: str | None = None) -> SchedulerHeartbeat:
    now = datetime.datetime.utcnow()
    row = session.get(SchedulerHeartbeat, _COMPONENT)
    if row is None:
        row = SchedulerHeartbeat(component=_COMPONENT, last_seen_at=now, last_run_type=run_type, last_ticker=ticker)
        session.add(row)
    else:
        row.last_seen_at = now
        row.last_run_type = run_type
        row.last_ticker = ticker
    session.flush()
    return row


def get_heartbeat(session: Session) -> SchedulerHeartbeat | None:
    return session.get(SchedulerHeartbeat, _COMPONENT)
```

- [ ] **Step 5: Apply the schema change to the dev database**

Run: `.venv\Scripts\python.exe -m tradingsystem.db.init_db`
Expected: `Second-brain schema created.` (creates the new `scheduler_heartbeats` table; existing tables are untouched since `create_all` skips tables that already exist)

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_heartbeat.py -v`
Expected: all 3 tests PASS

- [ ] **Step 7: Run the full suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/tradingsystem/db/models.py src/tradingsystem/orchestration/__init__.py src/tradingsystem/orchestration/heartbeat.py tests/test_heartbeat.py
git commit -m "Add SchedulerHeartbeat table and heartbeat read/write helpers"
```

---

### Task 3: `discord_alerts.py`

**Files:**
- Create: `src/tradingsystem/orchestration/discord_alerts.py`
- Test: `tests/test_discord_alerts.py`

**Interfaces:**
- Produces: `send_alert(settings: Settings, message: str, level: str = "info") -> None`
- Consumes: `tradingsystem.config.Settings.discord_webhook_url` (existing field)

- [ ] **Step 1: Write the failing test**

Create `tests/test_discord_alerts.py`:

```python
import httpx
import pytest

from tradingsystem.config import Settings
from tradingsystem.orchestration import discord_alerts


class _CapturingPost:
    def __init__(self, raise_error=None):
        self.calls = []
        self.raise_error = raise_error

    def __call__(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout))
        if self.raise_error is not None:
            raise self.raise_error
        return httpx.Response(204, request=httpx.Request("POST", url))


def test_send_alert_posts_to_configured_webhook(monkeypatch):
    fake_post = _CapturingPost()
    monkeypatch.setattr(discord_alerts.httpx, "post", fake_post)
    settings = Settings(discord_webhook_url="https://discord.example/webhook")

    discord_alerts.send_alert(settings, "order placed", level="info")

    assert len(fake_post.calls) == 1
    url, payload, _ = fake_post.calls[0]
    assert url == "https://discord.example/webhook"
    assert "[INFO] order placed" in payload["content"]


def test_send_alert_noop_when_webhook_not_configured(monkeypatch):
    fake_post = _CapturingPost()
    monkeypatch.setattr(discord_alerts.httpx, "post", fake_post)
    settings = Settings(discord_webhook_url="")

    discord_alerts.send_alert(settings, "should not send", level="critical")

    assert fake_post.calls == []


def test_send_alert_swallows_http_errors(monkeypatch):
    fake_post = _CapturingPost(raise_error=httpx.ConnectError("refused"))
    monkeypatch.setattr(discord_alerts.httpx, "post", fake_post)
    settings = Settings(discord_webhook_url="https://discord.example/webhook")

    discord_alerts.send_alert(settings, "network is down", level="critical")  # must not raise

    assert len(fake_post.calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_discord_alerts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradingsystem.orchestration.discord_alerts'`

- [ ] **Step 3: Write the implementation**

Create `src/tradingsystem/orchestration/discord_alerts.py`:

```python
"""Minimal Discord webhook sender — ARCHITECTURE.md §7.

Alerts immediately on: circuit breaker trips, order rejections/API errors, every
trade placed, and unhandled per-ticker cycle errors. Never raises — a Discord/network
failure must not take down the trading loop that's trying to report through it.
"""

from __future__ import annotations

import logging

import httpx

from tradingsystem.config import Settings

log = logging.getLogger(__name__)


def send_alert(settings: Settings, message: str, level: str = "info") -> None:
    if not settings.discord_webhook_url:
        log.info("[discord alert suppressed, no webhook configured] [%s] %s", level.upper(), message)
        return
    try:
        httpx.post(settings.discord_webhook_url, json={"content": f"[{level.upper()}] {message}"}, timeout=10)
    except httpx.HTTPError as exc:
        log.warning("failed to send Discord alert: %s", exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_discord_alerts.py -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/orchestration/discord_alerts.py tests/test_discord_alerts.py
git commit -m "Add minimal Discord webhook alert sender"
```

---

### Task 4: `cycle.py` — market-status and circuit-breaker gates

Builds the early-exit half of `run_full_cycle`: market-status check, snapshot baseline, circuit-breaker gate, and the shared per-ticker journaling helper for skipped cycles. The per-ticker research/trade loop is added in Task 5.

**Files:**
- Create: `src/tradingsystem/orchestration/cycle.py`
- Test: `tests/test_cycle.py`

**Interfaces:**
- Consumes: `heartbeat.record_heartbeat(session, run_type, ticker=None)` (Task 2), `discord_alerts.send_alert(settings, message, level)` (Task 3), `tradingsystem.db.repositories.get_active_breaker_event(session, breaker_type)` / `record_breaker_trip(session, breaker_type, trigger_reason)` (existing), `tradingsystem.risk.circuit_breaker.check_daily_breaker` / `check_weekly_breaker` (existing), `tradingsystem.config.RiskConfig` (existing)
- Produces: `run_full_cycle(session, alpaca_client, run_type, settings=None, risk_config=None, watchlist=None) -> None`, `BreakerGateResult(blocked: bool, tripped_types: list[str])`, `ensure_snapshot_baseline(session, alpaca_client) -> None`, `check_breakers(session, alpaca_client, risk_config, settings) -> BreakerGateResult` — all used directly by Task 5 and by `scheduler.py` (Task 6)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cycle.py`:

```python
import datetime

from tradingsystem.config import RiskConfig
from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, PortfolioSnapshot
from tradingsystem.execution.alpaca_client import AccountSnapshot
from tradingsystem.orchestration import cycle


class FakeAlpacaClient:
    def __init__(self, equity=100_000.0, cash=80_000.0, positions=None, open_orders=None,
                 market_status="open", price=100.0, price_overrides=None,
                 raise_on_price_for=None, submit_response=None):
        self.equity = equity
        self.cash = cash
        self.positions = positions or {}
        self.open_orders = open_orders or set()
        self.market_status = market_status
        self.price = price
        self.price_overrides = price_overrides or {}
        self.raise_on_price_for = raise_on_price_for or set()
        self.submit_response = submit_response
        self.submit_calls = []

    def get_account(self):
        return AccountSnapshot(equity=self.equity, cash=self.cash)

    def get_positions(self):
        return self.positions

    def get_open_orders(self):
        return self.open_orders

    def get_clock(self):
        return self.market_status

    def get_latest_price(self, ticker):
        if ticker in self.raise_on_price_for:
            raise RuntimeError(f"simulated price fetch failure for {ticker}")
        return self.price_overrides.get(ticker, self.price)

    def submit_limit_order(self, ticker, side, qty, limit_price):
        self.submit_calls.append((ticker, side, qty, limit_price))
        return self.submit_response

    def get_order(self, alpaca_order_id):
        raise NotImplementedError

    def cancel_order(self, alpaca_order_id):
        pass


RISK_CONFIG = RiskConfig(
    max_position_pct=0.10,
    cash_reserve_pct=0.20,
    stop_loss_pct=0.08,
    daily_drawdown_breaker_pct=0.03,
    weekly_drawdown_breaker_pct=0.08,
    stale_data_max_age_minutes=15,
)

WATCHLIST = ["AAPL", "MSFT"]


def test_market_closed_journals_all_tickers_and_skips_everything_else(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    client = FakeAlpacaClient(market_status="closed")

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    runs = db_session.query(AgentRun).all()
    assert {r.ticker for r in runs} == set(WATCHLIST)
    assert all(r.outcome == "market_closed" for r in runs)
    assert db_session.query(PortfolioSnapshot).count() == 0
    assert alerts == []


def test_first_cycle_of_day_snapshots_baseline_and_does_not_trip(db_session):
    client = FakeAlpacaClient(market_status="open", equity=100_000.0)

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    snapshots = db_session.query(PortfolioSnapshot).all()
    assert len(snapshots) == 1
    assert float(snapshots[0].equity) == 100_000.0
    assert db_session.query(AgentRun).count() == 0  # no skip-journal rows written; gate passed


def test_active_daily_breaker_journals_and_skips_and_alerts(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    today = datetime.datetime.now(cycle._NY).date()
    db_session.add(PortfolioSnapshot(snapshot_date=today, equity=100_000.0, cash=80_000.0, positions={}))
    db_session.flush()
    client = FakeAlpacaClient(market_status="open", equity=96_000.0)  # 4% drawdown > 3% daily threshold

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    runs = db_session.query(AgentRun).all()
    assert {r.ticker for r in runs} == set(WATCHLIST)
    assert all(r.outcome == "circuit_breaker_active" for r in runs)
    events = db_session.query(CircuitBreakerEvent).all()
    assert len(events) == 1
    assert events[0].breaker_type == "daily"
    assert any(level == "critical" and "CIRCUIT BREAKER TRIPPED" in message for level, message in alerts)


def test_already_tripped_breaker_blocks_without_re_recording(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    today = datetime.datetime.now(cycle._NY).date()
    db_session.add(PortfolioSnapshot(snapshot_date=today, equity=100_000.0, cash=80_000.0, positions={}))
    db_session.add(CircuitBreakerEvent(breaker_type="daily", trigger_reason="already tripped earlier today"))
    db_session.flush()
    client = FakeAlpacaClient(market_status="open", equity=99_000.0)  # only 1% drawdown now, but breaker still active

    cycle.run_full_cycle(db_session, client, "midday", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    assert db_session.query(CircuitBreakerEvent).count() == 1  # not re-recorded
    runs = db_session.query(AgentRun).all()
    assert all(r.outcome == "circuit_breaker_active" for r in runs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cycle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradingsystem.orchestration.cycle'`

- [ ] **Step 3: Write the implementation**

Create `src/tradingsystem/orchestration/cycle.py`:

```python
"""Composes the decision-engine, risk, and execution modules into one scheduled
cycle — ARCHITECTURE.md §2/§6. Framework-free (no APScheduler import here) so it's
directly unit-testable; scheduler.py is the only caller that wires this to cron.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import zoneinfo

from sqlalchemy.orm import Session

from tradingsystem.config import RiskConfig, Settings, load_risk_config, load_watchlist
from tradingsystem.db.models import AgentRun, PortfolioSnapshot
from tradingsystem.db.repositories import get_active_breaker_event, record_breaker_trip
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol
from tradingsystem.orchestration import discord_alerts, heartbeat
from tradingsystem.risk.circuit_breaker import check_daily_breaker, check_weekly_breaker

log = logging.getLogger(__name__)

_NY = zoneinfo.ZoneInfo("America/New_York")


def _ny_today() -> datetime.date:
    return datetime.datetime.now(_NY).date()


def _ny_monday_of_this_week(today: datetime.date) -> datetime.date:
    return today - datetime.timedelta(days=today.weekday())


def _journal_skip(session: Session, watchlist: list[str], run_type: str, market_status: str, outcome: str) -> None:
    now = datetime.datetime.utcnow()
    for ticker in watchlist:
        session.add(AgentRun(
            ticker=ticker, run_type=run_type, started_at=now, finished_at=now,
            market_status=market_status, outcome=outcome,
        ))
    session.commit()


def ensure_snapshot_baseline(session: Session, alpaca_client: AlpacaClientProtocol) -> None:
    today = _ny_today()
    existing = session.query(PortfolioSnapshot).filter_by(snapshot_date=today).one_or_none()
    if existing is not None:
        return
    account = alpaca_client.get_account()
    session.add(PortfolioSnapshot(
        snapshot_date=today, equity=account.equity, cash=account.cash,
        positions=alpaca_client.get_positions(),
    ))
    session.flush()


@dataclasses.dataclass(frozen=True)
class BreakerGateResult:
    blocked: bool
    tripped_types: list[str]


def check_breakers(
    session: Session, alpaca_client: AlpacaClientProtocol, risk_config: RiskConfig, settings: Settings
) -> BreakerGateResult:
    today = _ny_today()
    monday = _ny_monday_of_this_week(today)

    day_start = session.query(PortfolioSnapshot).filter_by(snapshot_date=today).one()
    week_start = (
        session.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.snapshot_date >= monday)
        .order_by(PortfolioSnapshot.snapshot_date.asc())
        .first()
    )
    current_equity = alpaca_client.get_account().equity

    daily = check_daily_breaker(float(day_start.equity), current_equity, risk_config.daily_drawdown_breaker_pct)
    weekly = check_weekly_breaker(float(week_start.equity), current_equity, risk_config.weekly_drawdown_breaker_pct)

    for result in (daily, weekly):
        if result.tripped and get_active_breaker_event(session, result.breaker_type) is None:
            record_breaker_trip(
                session, result.breaker_type,
                trigger_reason=(
                    f"{result.breaker_type} drawdown {result.drawdown_pct:.4f} "
                    f"exceeded threshold {result.threshold_pct:.4f}"
                ),
            )
            discord_alerts.send_alert(
                settings,
                f"CIRCUIT BREAKER TRIPPED: {result.breaker_type} drawdown "
                f"{result.drawdown_pct:.2%} exceeds {result.threshold_pct:.2%}",
                level="critical",
            )

    tripped_types = []
    if daily.tripped or get_active_breaker_event(session, "daily") is not None:
        tripped_types.append("daily")
    if weekly.tripped or get_active_breaker_event(session, "weekly") is not None:
        tripped_types.append("weekly")

    return BreakerGateResult(blocked=bool(tripped_types), tripped_types=tripped_types)


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

    market_status = alpaca_client.get_clock()
    if market_status != "open":
        _journal_skip(session, watchlist, run_type, market_status, outcome="market_closed")
        return

    ensure_snapshot_baseline(session, alpaca_client)
    session.commit()

    gate = check_breakers(session, alpaca_client, risk_config, settings)
    session.commit()
    if gate.blocked:
        _journal_skip(session, watchlist, run_type, market_status, outcome="circuit_breaker_active")
        return

    # Per-ticker research/trade loop — Task 5.

    heartbeat.record_heartbeat(session, run_type)
    session.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cycle.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/orchestration/cycle.py tests/test_cycle.py
git commit -m "Add cycle.py market-status and circuit-breaker gates"
```

---

### Task 5: `cycle.py` — per-ticker research/trade loop

Completes `run_full_cycle` by replacing the `# Per-ticker research/trade loop — Task 5.` placeholder with the real loop: research → size → execute → alert, with per-ticker exception isolation.

**Files:**
- Modify: `src/tradingsystem/orchestration/cycle.py`
- Test: `tests/test_cycle.py` (add cases)

**Interfaces:**
- Consumes: `tradingsystem.decision_engine.runner.run_research` (existing, returns `ResearchResult` with `.decision_id` from Task 1), `tradingsystem.risk.position_sizing.size_order` (existing), `tradingsystem.execution.executor.build_portfolio_state` / `place_order` (existing)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cycle.py`. This needs a scripted `TradingAgentsGraph` stand-in, same pattern as `tests/test_decision_engine_runner.py`:

```python
from tradingsystem.db.models import Decision, Order
from tradingsystem.decision_engine import runner as runner_module


def make_final_state(final_trade_decision="Buy: strong fundamentals"):
    return {
        "market_report": "market report",
        "sentiment_report": "sentiment report",
        "news_report": "news report",
        "fundamentals_report": "fundamentals report",
        "investment_debate_state": {"bull_history": "bull", "bear_history": "bear", "judge_decision": "judge"},
        "trader_investment_plan": "trader plan",
        "risk_debate_state": {
            "aggressive_history": "aggressive", "conservative_history": "conservative",
            "neutral_history": "neutral", "judge_decision": "risk judge",
        },
        "investment_plan": "investment plan",
        "final_trade_decision": final_trade_decision,
    }


class ScriptedGraph:
    calls = []

    def __init__(self, debug=False, config=None):
        pass

    def propagate(self, ticker, trade_date):
        outcome = ScriptedGraph.calls.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_hold_decision_places_no_order(db_session, monkeypatch):
    ScriptedGraph.calls = [(make_final_state("Hold: no clear edge"), "Hold")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)
    client = FakeAlpacaClient(market_status="open")

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=["AAPL"])

    assert db_session.query(Decision).filter_by(decision="hold").count() == 1
    assert db_session.query(Order).count() == 0
    assert client.submit_calls == []


def test_buy_decision_sizes_and_places_order(db_session, monkeypatch):
    ScriptedGraph.calls = [(make_final_state("Buy: strong fundamentals"), "Buy")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    from tradingsystem.execution.alpaca_client import SubmittedOrder
    client = FakeAlpacaClient(market_status="open", equity=100_000.0, price=100.0,
                               submit_response=SubmittedOrder(alpaca_order_id="abc123", status="new"))

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=["AAPL"])

    assert client.submit_calls == [("AAPL", "buy", 100, 100.0)]
    order = db_session.query(Order).filter_by(alpaca_order_id="abc123").one()
    assert order.ticker == "AAPL"
    assert any(level == "info" and "Order placed" in message for level, message in alerts)


def test_one_ticker_exception_does_not_stop_the_others(db_session, monkeypatch):
    ScriptedGraph.calls = [
        (make_final_state("Buy: strong fundamentals"), "Buy"),
        (make_final_state("Buy: strong fundamentals"), "Buy"),
    ]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    from tradingsystem.execution.alpaca_client import SubmittedOrder
    client = FakeAlpacaClient(market_status="open", equity=100_000.0, price=100.0,
                               raise_on_price_for={"AAPL"},
                               submit_response=SubmittedOrder(alpaca_order_id="xyz789", status="new"))

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=["AAPL", "MSFT"])

    assert client.submit_calls == [("MSFT", "buy", 100, 100.0)]
    assert db_session.query(Order).count() == 1
    assert any(level == "critical" and "AAPL" in message for level, message in alerts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cycle.py -v`
Expected: FAIL — `test_hold_decision_places_no_order`, `test_buy_decision_sizes_and_places_order`, and `test_one_ticker_exception_does_not_stop_the_others` all fail because no research/trade loop runs yet (no `Decision`/`Order` rows get created)

- [ ] **Step 3: Implement the loop**

In `src/tradingsystem/orchestration/cycle.py`, add these imports at the top:

```python
from tradingsystem.decision_engine.runner import run_research
from tradingsystem.execution.executor import build_portfolio_state, place_order
from tradingsystem.risk.position_sizing import size_order
```

Replace the `    # Per-ticker research/trade loop — Task 5.` line inside `run_full_cycle` with:

```python
    for ticker in watchlist:
        try:
            result = run_research(
                session, ticker, trade_date=_ny_today().isoformat(),
                market_status=market_status, settings=settings,
            )
            heartbeat.record_heartbeat(session, run_type, ticker=ticker)
            session.commit()

            if not result.ok or result.decision == "hold":
                continue

            portfolio = build_portfolio_state(alpaca_client)
            price = alpaca_client.get_latest_price(ticker)
            proposal = size_order(
                result.rating, ticker, portfolio, price,
                risk_config.max_position_pct, data_timestamp=datetime.datetime.utcnow(),
            )
            if proposal is not None:
                exec_result = place_order(
                    session, alpaca_client, proposal, result.decision_id,
                    settings.kill_switch_file, risk_config.max_position_pct,
                    risk_config.cash_reserve_pct, risk_config.stale_data_max_age_minutes,
                )
                if exec_result.submitted:
                    discord_alerts.send_alert(
                        settings,
                        f"Order placed: {proposal.side} {proposal.qty} {ticker} @ "
                        f"{proposal.limit_price:.2f} (alpaca_order_id={exec_result.alpaca_order_id})",
                        level="info",
                    )
                else:
                    discord_alerts.send_alert(
                        settings,
                        f"Order rejected/failed for {ticker}: "
                        f"{exec_result.rejection_reasons or exec_result.error}",
                        level="warning",
                    )
            session.commit()
        except Exception as exc:  # noqa: BLE001 - one ticker's crash must not stop the rest of the cycle
            log.critical("unhandled error processing %s: %s", ticker, exc)
            discord_alerts.send_alert(settings, f"Cycle error on {ticker}: {exc}", level="critical")
            session.rollback()
            continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cycle.py -v`
Expected: all 7 tests PASS

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/orchestration/cycle.py tests/test_cycle.py
git commit -m "Complete run_full_cycle with per-ticker research/size/execute loop"
```

---

### Task 6: `scheduler.py` + cron `Settings` fields + `apscheduler` dependency

**Files:**
- Modify: `pyproject.toml` (add dependency)
- Modify: `src/tradingsystem/config.py` (add two `Settings` fields)
- Create: `src/tradingsystem/orchestration/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `cycle.run_full_cycle` (Task 5), `tradingsystem.config.Settings.pre_market_cron` / `.midday_cron` (new)
- Produces: `build_scheduler(settings: Settings | None = None) -> BlockingScheduler`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to the `dependencies` list (after `"alpaca-py>=0.30"`):

```toml
    "apscheduler>=3.10",
```

Run: `.venv\Scripts\python.exe -m pip install -e ".[dev]"`
Expected: installs `APScheduler` and its dependencies (`tzlocal`) into the venv

- [ ] **Step 2: Add the Settings fields**

In `src/tradingsystem/config.py`, add inside the `Settings` class, after the `tradingagents_run_max_attempts` field:

```python
    # Orchestration schedule (ARCHITECTURE.md §6) — standard 5-field cron syntax,
    # always interpreted in America/New_York regardless of host machine locale.
    pre_market_cron: str = "0 8 * * mon-fri"
    midday_cron: str = "30 12 * * mon-fri"
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_scheduler.py`:

```python
from apscheduler.triggers.cron import CronTrigger

from tradingsystem.config import Settings
from tradingsystem.orchestration.scheduler import build_scheduler


def test_settings_default_cron_values():
    settings = Settings()
    assert settings.pre_market_cron == "0 8 * * mon-fri"
    assert settings.midday_cron == "30 12 * * mon-fri"


def test_build_scheduler_registers_two_cron_jobs():
    settings = Settings(pre_market_cron="0 8 * * mon-fri", midday_cron="30 12 * * mon-fri")
    scheduler = build_scheduler(settings)

    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"pre_market_cycle", "midday_cycle"}
    assert isinstance(jobs["pre_market_cycle"].trigger, CronTrigger)
    assert isinstance(jobs["midday_cycle"].trigger, CronTrigger)
    assert jobs["pre_market_cycle"].args == ["pre_market"]
    assert jobs["midday_cycle"].args == ["midday"]
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradingsystem.orchestration.scheduler'`

- [ ] **Step 5: Write the implementation**

Create `src/tradingsystem/orchestration/scheduler.py`:

```python
"""APScheduler wiring for the twice-daily research/trade cycle — ARCHITECTURE.md §6.

No business logic here — run_full_cycle (cycle.py) is framework-free and
independently testable; this module's only job is cron scheduling.

Entry point: python -m tradingsystem.orchestration.scheduler
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from tradingsystem.config import Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.execution.alpaca_client import AlpacaClient
from tradingsystem.orchestration.cycle import run_full_cycle

log = logging.getLogger(__name__)

_TIMEZONE = "America/New_York"


def _run(run_type: str) -> None:
    settings = Settings()
    session = make_session_factory()()
    client = AlpacaClient(settings)
    try:
        run_full_cycle(session, client, run_type, settings=settings)
    except Exception:
        log.critical("run_full_cycle(%s) raised unhandled — scheduler continuing", run_type, exc_info=True)
        session.rollback()
    finally:
        session.close()


def build_scheduler(settings: Settings | None = None) -> BlockingScheduler:
    settings = settings or Settings()
    scheduler = BlockingScheduler(timezone=_TIMEZONE)
    scheduler.add_job(
        _run, CronTrigger.from_crontab(settings.pre_market_cron, timezone=_TIMEZONE),
        args=["pre_market"], id="pre_market_cycle",
    )
    scheduler.add_job(
        _run, CronTrigger.from_crontab(settings.midday_cron, timezone=_TIMEZONE),
        args=["midday"], id="midday_cycle",
    )
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_scheduler().start()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_scheduler.py -v`
Expected: both tests PASS

- [ ] **Step 7: Run the full suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests PASS (60 total: 51 pre-existing + 3 heartbeat + 3 discord_alerts + 7 cycle + 2 scheduler, plus the extended decision_engine_runner assertion)

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/tradingsystem/config.py src/tradingsystem/orchestration/scheduler.py tests/test_scheduler.py
git commit -m "Add APScheduler wiring for twice-daily pre_market/midday cycles"
```

---

## Self-Review

**Spec coverage:**
- §2 module layout (`cycle.py`, `scheduler.py`, `heartbeat.py`, `discord_alerts.py`) → Tasks 2–6.
- §3 upstream `decision_id` change → Task 1.
- §4 `SchedulerHeartbeat` table → Task 2.
- §5 `Settings` cron fields → Task 6.
- §6 per-cycle pipeline (heartbeat → market check → snapshot → breaker gate → per-ticker loop → final heartbeat) → Tasks 4–5, in the exact order specified.
- §7 Discord alert triggers (breaker trip, order rejected/error, every trade placed, unhandled per-ticker exception) → Task 3 (sender) + Tasks 4/5 (call sites) all covered.
- §9 testing approach (real Postgres via `db_session`, scripted `TradingAgentsGraph`, mocked `AlpacaClientProtocol`) → used throughout.

**Placeholder scan:** No TBD/TODO. Every step has runnable code, not a description of code.

**Type consistency:** `ResearchResult.decision_id` (Task 1) flows into `place_order`'s `decision_id` parameter (Task 5) unchanged. `BreakerGateResult` (Task 4) is constructed and consumed only within `cycle.py`. `heartbeat.record_heartbeat`/`get_heartbeat` signatures (Task 2) match their call sites in `cycle.py` (Task 4) and are not touched again elsewhere. `discord_alerts.send_alert(settings, message, level=...)` (Task 3) signature matches every call site added in Tasks 4 and 5.
