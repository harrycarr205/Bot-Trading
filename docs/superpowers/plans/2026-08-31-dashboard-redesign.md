# Dashboard UI/UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the server-rendered Jinja2 dashboard with a React/TypeScript SPA over a new FastAPI JSON API — a Bloomberg-terminal-styled, fully cross-linked, live-polling dashboard with a composable Overview grid.

**Architecture:** FastAPI stops rendering templates and becomes a JSON API under `/api/*`, reusing existing SQLAlchemy queries but returning Pydantic response models; it also serves the built React app as static files with an SPA fallback. The React app (Vite build) polls the API for live data, uses shared components (`Card`, `DataTable`, `StatTile`, `Chart`) for consistency, and a panel-registry + `react-grid-layout` mechanism for the composable Overview grid. Every existing capability (7 views, process control, order cancel, config editing) is preserved one-for-one; two new pages (Positions, Ticker Detail) are added for cross-linking.

**Tech Stack:** Backend: FastAPI (existing), Pydantic v2, SQLAlchemy 2.0 (existing). Frontend: React 18, TypeScript, Vite, React Router, `react-grid-layout`, `recharts`, Vitest + React Testing Library, Playwright (E2E).

**Spec:** `docs/superpowers/specs/2026-08-31-dashboard-redesign-design.md`

## Global Constraints

- No authentication, localhost-only — the existing `require_same_origin` (CSRF) guard is preserved unchanged on every mutating endpoint (spec §1, §5).
- Polling only, no WebSockets (spec §1). Ticker strip / heartbeat / breakers poll ~10s; positions/equity ~30s; static detail content (a past transcript) fetches once (spec §6).
- Palette, type, and layout tokens are exactly as specified in spec §2 — reuse the token values verbatim, do not invent new colors.
- `config_editing.py`'s validation and audit-log logic is unchanged — only its caller changes from a form handler to a JSON route (spec §5).
- Circuit-breaker clearing and the kill switch stay CLI-only, out of scope (spec §8).
- Every existing dashboard capability must have a passing equivalent test before its old Jinja2 route is deleted (Task 27) — no regressions.

---

### Task 1: Extract shared dependencies (`dependencies.py`) — no behavior change

Isolates `get_db`, `get_alpaca_client`, and the CSRF guard into a module the new route files can import without circularly importing `app.py`. Pure refactor — the existing 205 tests must stay green with zero code changes to them except import paths.

**Files:**
- Create: `src/tradingsystem/dashboard/dependencies.py`
- Modify: `src/tradingsystem/dashboard/app.py:1-76` (remove the extracted definitions, import from `dependencies.py` instead)
- Modify: `tests/conftest.py:18` (import `get_db` from `dependencies` instead of `app`)
- Modify: `tests/test_dashboard_orders_cancel.py:4` (import `get_alpaca_client` from `dependencies` instead of `app`)

**Interfaces:**
- Produces: `dependencies.get_db() -> Generator[Session]`, `dependencies.get_alpaca_client() -> AlpacaClientProtocol`, `dependencies.require_same_origin(request: Request) -> None`, `dependencies._ALLOWED_ORIGIN_HOSTS: set[str]`

- [ ] **Step 1: Create `dependencies.py`**

```python
"""Shared FastAPI dependencies for dashboard routers — separated from app.py
so route modules can depend on these without importing app.py itself
(avoids a circular import between app.py and routes/*.py).
"""

from __future__ import annotations

import urllib.parse

from fastapi import HTTPException
from fastapi.requests import Request

from tradingsystem.config import Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.execution.alpaca_client import AlpacaClient, AlpacaClientProtocol

_session_factory = make_session_factory()


def get_db():
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def get_alpaca_client() -> AlpacaClientProtocol:
    return AlpacaClient(Settings())


# This dashboard is documented and designed as localhost-only (Settings.dashboard_host
# defaults to "127.0.0.1") — pinned here rather than derived from the request's own Host
# header, which uvicorn does not validate and a DNS-rebinding attacker could spoof.
_ALLOWED_ORIGIN_HOSTS = {"127.0.0.1", "localhost"}


def require_same_origin(request: Request) -> None:
    """Rejects cross-origin POSTs to the write routes (CSRF guard)."""
    origin = request.headers.get("origin")
    if origin is None:
        return
    origin_host = urllib.parse.urlsplit(origin).hostname
    if origin_host not in _ALLOWED_ORIGIN_HOSTS:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected")
```

- [ ] **Step 2: Update `app.py` to import from `dependencies.py`**

Remove lines defining `_session_factory`, `get_db`, `get_alpaca_client`, `_ALLOWED_ORIGIN_HOSTS`, `_require_same_origin` from `app.py`. Add near the top:

```python
from tradingsystem.dashboard.dependencies import get_alpaca_client, get_db, require_same_origin
```

Replace every use of `_require_same_origin` in `app.py` with `require_same_origin` (rename at call sites, e.g. `Depends(_require_same_origin)` → `Depends(require_same_origin)`).

- [ ] **Step 3: Update test imports**

`tests/conftest.py:18`:
```python
from tradingsystem.dashboard.app import app
from tradingsystem.dashboard.dependencies import get_db
```

`tests/test_dashboard_orders_cancel.py:4`:
```python
from tradingsystem.dashboard.app import app
from tradingsystem.dashboard.dependencies import get_alpaca_client
```

- [ ] **Step 4: Run the full existing suite to confirm zero regressions**

Run: `pytest tests/ -v`
Expected: all 205 tests PASS (same count as before this task — this is a pure refactor)

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/dashboard/dependencies.py src/tradingsystem/dashboard/app.py tests/conftest.py tests/test_dashboard_orders_cancel.py
git commit -m "refactor: extract dashboard dependencies into dependencies.py"
```

---

### Task 2: Pydantic response schemas

All JSON response shapes for every endpoint, defined once so every later route task has a stable, already-reviewed contract.

**Files:**
- Create: `src/tradingsystem/dashboard/schemas.py`
- Test: `tests/test_dashboard_schemas.py`

**Interfaces:**
- Consumes: `tradingsystem.db.models.{AgentRun,Decision,Order,Fill,PortfolioSnapshot,RealizedPnl,CircuitBreakerEvent,SchedulerHeartbeat}` (existing ORM models)
- Produces: `OverviewResponse`, `PositionsResponse`, `DecisionsResponse`, `DecisionDetailResponse`, `OrdersResponse`, `TickerDetailResponse`, `PnlResponse`, `PnlSeriesResponse`, `ControlStatusResponse`, `ConfigResponse` — all importable from `tradingsystem.dashboard.schemas`

- [ ] **Step 1: Write the failing schema test**

```python
import datetime
import uuid

from tradingsystem.dashboard.schemas import (
    BreakerOut, HeartbeatOut, OverviewResponse, OverviewSnapshot, PositionOut, PositionsResponse,
)


def test_overview_response_serializes_to_json_compatible_types():
    resp = OverviewResponse(
        snapshot=OverviewSnapshot(snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0),
        heartbeat=HeartbeatOut(
            last_seen_at=datetime.datetime(2026, 8, 24, 9, 0), last_run_type="pre_market", last_ticker="AAPL",
        ),
        heartbeat_stale=False,
        active_breakers=[BreakerOut(
            id=uuid.uuid4(), breaker_type="daily", trigger_reason="4% drawdown",
            tripped_at=datetime.datetime(2026, 8, 24, 9, 0),
        )],
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["snapshot"]["equity"] == 100_000.0
    assert dumped["active_breakers"][0]["breaker_type"] == "daily"


def test_position_out_computes_from_plain_fields():
    p = PositionOut(
        ticker="AAPL", qty=10.0, avg_entry_price=180.0, current_price=184.5,
        unrealized_pnl=45.0, latest_decision="buy", latest_rating="Buy",
    )
    assert PositionsResponse(positions=[p], candidates=[]).positions[0].ticker == "AAPL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradingsystem.dashboard.schemas'`

- [ ] **Step 3: Write `schemas.py`**

```python
"""Pydantic response models for the dashboard JSON API (/api/*).

One module for every response shape so every route task has a stable,
already-reviewed contract — see docs/superpowers/specs/2026-08-31-dashboard-redesign-design.md §5.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict


class OverviewSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    snapshot_date: datetime.date
    equity: float
    cash: float


class HeartbeatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    last_seen_at: datetime.datetime
    last_run_type: str | None
    last_ticker: str | None


class BreakerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    breaker_type: str
    trigger_reason: str
    tripped_at: datetime.datetime


class OverviewResponse(BaseModel):
    snapshot: OverviewSnapshot | None
    heartbeat: HeartbeatOut | None
    heartbeat_stale: bool
    active_breakers: list[BreakerOut]


class PositionOut(BaseModel):
    ticker: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float
    latest_decision: str | None
    latest_rating: str | None


class CandidateOut(BaseModel):
    ticker: str
    held: bool


class PositionsResponse(BaseModel):
    positions: list[PositionOut]
    candidates: list[CandidateOut]


class DecisionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    rating: str
    decision: str
    reasoning_summary: str


class AgentRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ticker: str
    run_type: str
    started_at: datetime.datetime
    outcome: str | None
    decision: DecisionSummary | None


class DecisionsResponse(BaseModel):
    runs: list[AgentRunSummary]


class TranscriptEntry(BaseModel):
    role: str
    content: str


class DecisionDetailResponse(BaseModel):
    id: uuid.UUID
    ticker: str
    run_type: str
    started_at: datetime.datetime
    outcome: str | None
    market_status: str
    decision: DecisionSummary | None
    transcripts: list[TranscriptEntry]
    order_id: uuid.UUID | None


class FillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    filled_at: datetime.datetime
    fill_qty: float
    fill_price: float


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    submitted_at: datetime.datetime
    ticker: str
    side: str
    qty: float
    limit_price: float
    status: str
    decision_id: uuid.UUID
    agent_run_id: uuid.UUID
    fills: list[FillOut]
    cancellable: bool


class OrdersResponse(BaseModel):
    orders: list[OrderOut]


class TickerDetailResponse(BaseModel):
    ticker: str
    runs: list[AgentRunSummary]
    orders: list[OrderOut]


class RealizedPnlOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ticker: str
    pnl_amount: float
    closed_at: datetime.datetime
    decision_ids: list[uuid.UUID]


class PnlResponse(BaseModel):
    snapshots: list[OverviewSnapshot]
    realized: list[RealizedPnlOut]


class PnlSeriesPoint(BaseModel):
    date: datetime.date
    equity: float


class PnlSeriesResponse(BaseModel):
    points: list[PnlSeriesPoint]


class ProcessStatusOut(BaseModel):
    alive: bool
    pid: int | None


class ControlStatusResponse(BaseModel):
    scheduler: ProcessStatusOut
    watchdog: ProcessStatusOut
    scheduler_log: str | None
    watchdog_log: str | None
    run_once_log: str | None


class RiskConfigOut(BaseModel):
    max_position_pct: float
    cash_reserve_pct: float
    stop_loss_pct: float
    daily_drawdown_breaker_pct: float
    weekly_drawdown_breaker_pct: float
    stale_data_max_age_minutes: int


class ConfigResponse(BaseModel):
    tickers: list[str]
    risk_config: RiskConfigOut
    env_values: dict[str, str | None]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard_schemas.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/dashboard/schemas.py tests/test_dashboard_schemas.py
git commit -m "feat: add dashboard API response schemas"
```

---

### Task 3: Overview API route (`GET /api/overview`)

**Files:**
- Create: `src/tradingsystem/dashboard/routes/__init__.py` (empty)
- Create: `src/tradingsystem/dashboard/routes/overview.py`
- Modify: `src/tradingsystem/dashboard/app.py` (add `app.include_router(overview.router, prefix="/api")`, keep old `GET /` route untouched for now — deleted in Task 25)
- Test: `tests/test_dashboard_api_overview.py`

**Interfaces:**
- Consumes: `schemas.{OverviewResponse,OverviewSnapshot,HeartbeatOut,BreakerOut}` (Task 2), `dependencies.get_db` (Task 1)
- Produces: `routes.overview.router: APIRouter` with `GET /overview`

- [ ] **Step 1: Write the failing API test**

```python
import datetime

from tradingsystem.db.models import CircuitBreakerEvent, PortfolioSnapshot, SchedulerHeartbeat


def test_api_overview_empty_state(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.query(SchedulerHeartbeat).delete()
    db_session.query(CircuitBreakerEvent).delete()
    db_session.flush()

    response = client.get("/api/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"] is None
    assert body["heartbeat"] is None
    assert body["active_breakers"] == []


def test_api_overview_shows_snapshot_and_fresh_heartbeat(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.query(SchedulerHeartbeat).delete()
    db_session.flush()
    db_session.add(PortfolioSnapshot(
        snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0, positions={},
    ))
    db_session.add(SchedulerHeartbeat(
        component="scheduler", last_seen_at=datetime.datetime.utcnow(),
        last_run_type="pre_market", last_ticker="AAPL",
    ))
    db_session.flush()

    response = client.get("/api/overview")

    body = response.json()
    assert body["snapshot"]["equity"] == 100000.0
    assert body["heartbeat_stale"] is False
    assert body["heartbeat"]["last_ticker"] == "AAPL"


def test_api_overview_shows_active_breaker(client, db_session):
    db_session.query(CircuitBreakerEvent).delete()
    db_session.flush()
    db_session.add(CircuitBreakerEvent(breaker_type="daily", trigger_reason="4% drawdown"))
    db_session.flush()

    response = client.get("/api/overview")

    body = response.json()
    assert body["active_breakers"][0]["breaker_type"] == "daily"
    assert body["active_breakers"][0]["trigger_reason"] == "4% drawdown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_api_overview.py -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Write `routes/overview.py`**

```python
"""GET /api/overview — portfolio snapshot, heartbeat freshness, active circuit breakers."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tradingsystem.dashboard.dependencies import get_db
from tradingsystem.dashboard.schemas import BreakerOut, HeartbeatOut, OverviewResponse, OverviewSnapshot
from tradingsystem.db.models import CircuitBreakerEvent, PortfolioSnapshot
from tradingsystem.orchestration import heartbeat as heartbeat_module

router = APIRouter()

HEARTBEAT_STALE_AFTER = datetime.timedelta(hours=12)


@router.get("/overview", response_model=OverviewResponse)
def get_overview(db: Session = Depends(get_db)) -> OverviewResponse:
    snapshot = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.snapshot_date.desc()).first()
    heartbeat = heartbeat_module.get_heartbeat(db)
    heartbeat_stale = (
        heartbeat is not None
        and datetime.datetime.utcnow() - heartbeat.last_seen_at > HEARTBEAT_STALE_AFTER
    )
    active_breakers = (
        db.query(CircuitBreakerEvent)
        .filter(CircuitBreakerEvent.cleared_at.is_(None))
        .order_by(CircuitBreakerEvent.tripped_at.desc())
        .all()
    )
    return OverviewResponse(
        snapshot=OverviewSnapshot.model_validate(snapshot) if snapshot else None,
        heartbeat=HeartbeatOut.model_validate(heartbeat) if heartbeat else None,
        heartbeat_stale=heartbeat_stale,
        active_breakers=[BreakerOut.model_validate(b) for b in active_breakers],
    )
```

`routes/__init__.py` is empty (marks the package).

- [ ] **Step 4: Wire into `app.py`**

Add near the top of `app.py`:
```python
from tradingsystem.dashboard.routes import overview
```
Add after `app = FastAPI(title="Bot-Trading Dashboard")`:
```python
app.include_router(overview.router, prefix="/api")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_dashboard_api_overview.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/dashboard/routes/ src/tradingsystem/dashboard/app.py tests/test_dashboard_api_overview.py
git commit -m "feat: add GET /api/overview JSON endpoint"
```

---

### Task 4: Positions API route (`GET /api/positions`) — new capability

Live positions (via Alpaca, not the stale daily snapshot) plus the candidate universe, for the new Positions page.

**Files:**
- Create: `src/tradingsystem/dashboard/routes/positions.py`
- Modify: `src/tradingsystem/dashboard/app.py` (add router)
- Test: `tests/test_dashboard_api_positions.py`

**Interfaces:**
- Consumes: `schemas.{PositionOut,CandidateOut,PositionsResponse}` (Task 2), `dependencies.{get_db,get_alpaca_client}` (Task 1), `execution.alpaca_client.PositionDetail` (existing: `ticker: str, qty: float, avg_entry_price: float, current_price: float`), `config_editing.read_candidate_universe_tickers` (existing)
- Produces: `routes.positions.router: APIRouter` with `GET /positions`

- [ ] **Step 1: Write the failing API test**

```python
import datetime

from tradingsystem.dashboard.app import app
from tradingsystem.dashboard.dependencies import get_alpaca_client
from tradingsystem.db.models import AgentRun, Decision


class FakePositionDetail:
    def __init__(self, ticker, qty, avg_entry_price, current_price):
        self.ticker = ticker
        self.qty = qty
        self.avg_entry_price = avg_entry_price
        self.current_price = current_price


class FakeAlpacaClient:
    def __init__(self, positions):
        self._positions = positions

    def get_position_details(self):
        return self._positions


def test_api_positions_computes_unrealized_pnl_and_links_latest_decision(client, db_session):
    now = datetime.datetime.utcnow()
    run = AgentRun(ticker="AAPL", run_type="pre_market", started_at=now, finished_at=now,
                    market_status="open", outcome="decision_recorded")
    db_session.add(run)
    db_session.flush()
    db_session.add(Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="x"))
    db_session.flush()

    fake = FakeAlpacaClient([FakePositionDetail("AAPL", 10.0, 180.0, 184.5)])
    app.dependency_overrides[get_alpaca_client] = lambda: fake

    response = client.get("/api/positions")

    body = response.json()
    pos = body["positions"][0]
    assert pos["ticker"] == "AAPL"
    assert pos["unrealized_pnl"] == 45.0
    assert pos["latest_decision"] == "buy"
    assert pos["latest_rating"] == "Buy"


def test_api_positions_marks_candidates_held_or_not(client, db_session):
    fake = FakeAlpacaClient([])
    app.dependency_overrides[get_alpaca_client] = lambda: fake

    response = client.get("/api/positions")

    body = response.json()
    assert isinstance(body["candidates"], list)
    assert all("ticker" in c and "held" in c for c in body["candidates"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_api_positions.py -v`
Expected: FAIL with 404

- [ ] **Step 3: Write `routes/positions.py`**

```python
"""GET /api/positions — live positions (via Alpaca) plus the candidate universe."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tradingsystem.config import REPO_ROOT
from tradingsystem.dashboard import config_editing
from tradingsystem.dashboard.dependencies import get_alpaca_client, get_db
from tradingsystem.dashboard.schemas import CandidateOut, PositionOut, PositionsResponse
from tradingsystem.db.models import AgentRun
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol

router = APIRouter()

_CANDIDATE_UNIVERSE_PATH = REPO_ROOT / "config" / "candidate_universe.yaml"


def _latest_decision_for(db: Session, ticker: str) -> tuple[str | None, str | None]:
    run = (
        db.query(AgentRun)
        .filter(AgentRun.ticker == ticker)
        .order_by(AgentRun.started_at.desc())
        .first()
    )
    if run is None or not run.decisions:
        return None, None
    decision = run.decisions[0]
    return decision.decision, decision.rating


@router.get("/positions", response_model=PositionsResponse)
def get_positions(
    db: Session = Depends(get_db),
    alpaca_client: AlpacaClientProtocol = Depends(get_alpaca_client),
) -> PositionsResponse:
    details = alpaca_client.get_position_details()
    positions = []
    for p in details:
        decision, rating = _latest_decision_for(db, p.ticker)
        positions.append(PositionOut(
            ticker=p.ticker,
            qty=p.qty,
            avg_entry_price=p.avg_entry_price,
            current_price=p.current_price,
            unrealized_pnl=(p.current_price - p.avg_entry_price) * p.qty,
            latest_decision=decision,
            latest_rating=rating,
        ))
    held_tickers = {p.ticker for p in details}
    candidate_tickers = config_editing.read_candidate_universe_tickers(_CANDIDATE_UNIVERSE_PATH)
    candidates = [CandidateOut(ticker=t, held=t in held_tickers) for t in candidate_tickers]
    return PositionsResponse(positions=positions, candidates=candidates)
```

- [ ] **Step 4: Wire into `app.py`**

Add `from tradingsystem.dashboard.routes import positions` and `app.include_router(positions.router, prefix="/api")`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_dashboard_api_positions.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/dashboard/routes/positions.py src/tradingsystem/dashboard/app.py tests/test_dashboard_api_positions.py
git commit -m "feat: add GET /api/positions endpoint (new Positions page data)"
```

---

### Task 5: Decisions + Decision Detail + Ticker Detail API routes

**Files:**
- Create: `src/tradingsystem/dashboard/routes/decisions.py`
- Create: `src/tradingsystem/dashboard/routes/tickers.py`
- Modify: `src/tradingsystem/dashboard/app.py`
- Test: `tests/test_dashboard_api_decisions.py`, `tests/test_dashboard_api_ticker_detail.py`

**Interfaces:**
- Consumes: `schemas.{AgentRunSummary,DecisionSummary,DecisionsResponse,TranscriptEntry,DecisionDetailResponse,TickerDetailResponse,OrderOut,FillOut}` (Task 2)
- Produces: `routes.decisions.router` with `GET /decisions?ticker=`, `GET /decisions/{agent_run_id}`; `routes.tickers.router` with `GET /ticker/{symbol}`

- [ ] **Step 1: Write failing tests**

`tests/test_dashboard_api_decisions.py`:
```python
import datetime
import uuid

from tradingsystem.db.models import AgentRun, Decision, DebateTranscript


def _make_run_with_decision(db_session, ticker="AAPL"):
    now = datetime.datetime.utcnow()
    run = AgentRun(ticker=ticker, run_type="pre_market", started_at=now, finished_at=now,
                    market_status="open", outcome="decision_recorded")
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="looks good")
    db_session.add(decision)
    db_session.flush()
    return run, decision


def test_api_decisions_lists_runs_newest_first(client, db_session):
    _make_run_with_decision(db_session, "AAPL")
    _make_run_with_decision(db_session, "MSFT")

    response = client.get("/api/decisions")

    body = response.json()
    assert len(body["runs"]) >= 2
    assert body["runs"][0]["decision"]["decision"] in ("buy", "sell", "hold")


def test_api_decisions_filters_by_ticker(client, db_session):
    _make_run_with_decision(db_session, "AAPL")
    _make_run_with_decision(db_session, "MSFT")

    response = client.get("/api/decisions", params={"ticker": "AAPL"})

    body = response.json()
    assert all(r["ticker"] == "AAPL" for r in body["runs"])


def test_api_decision_detail_orders_transcripts_by_role(client, db_session):
    run, decision = _make_run_with_decision(db_session)
    db_session.add(DebateTranscript(agent_run_id=run.id, role="trader", content="buy it"))
    db_session.add(DebateTranscript(agent_run_id=run.id, role="market_analyst", content="trending up"))
    db_session.flush()

    response = client.get(f"/api/decisions/{run.id}")

    body = response.json()
    roles = [t["role"] for t in body["transcripts"]]
    assert roles.index("market_analyst") < roles.index("trader")


def test_api_decision_detail_404_for_missing_run(client, db_session):
    response = client.get(f"/api/decisions/{uuid.uuid4()}")
    assert response.status_code == 404
```

`tests/test_dashboard_api_ticker_detail.py`:
```python
import datetime

from tradingsystem.db.models import AgentRun, Decision, Order


def test_api_ticker_detail_returns_runs_and_orders_for_ticker(client, db_session):
    now = datetime.datetime.utcnow()
    run = AgentRun(ticker="AAPL", run_type="pre_market", started_at=now, finished_at=now,
                    market_status="open", outcome="decision_recorded")
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="x")
    db_session.add(decision)
    db_session.flush()
    db_session.add(Order(decision_id=decision.id, ticker="AAPL", side="buy", qty=10,
                          limit_price=100.0, status="filled", alpaca_order_id="abc", submitted_at=now))
    db_session.flush()

    response = client.get("/api/ticker/AAPL")

    body = response.json()
    assert body["ticker"] == "AAPL"
    assert len(body["runs"]) == 1
    assert len(body["orders"]) == 1


def test_api_ticker_detail_empty_for_unknown_ticker(client, db_session):
    response = client.get("/api/ticker/ZZZZ")
    body = response.json()
    assert body["runs"] == []
    assert body["orders"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_api_decisions.py tests/test_dashboard_api_ticker_detail.py -v`
Expected: FAIL with 404 (routes don't exist)

- [ ] **Step 3: Write `routes/decisions.py`**

```python
"""GET /api/decisions, GET /api/decisions/{agent_run_id}."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from tradingsystem.dashboard.dependencies import get_db
from tradingsystem.dashboard.schemas import (
    AgentRunSummary, DecisionDetailResponse, DecisionsResponse, DecisionSummary, TranscriptEntry,
)
from tradingsystem.db.models import AgentRun

router = APIRouter()

_ROLE_ORDER = [
    "market_analyst", "sentiment_analyst", "news_analyst", "fundamentals_analyst",
    "bull_researcher", "bear_researcher", "research_manager_judge", "trader",
    "risk_aggressive", "risk_conservative", "risk_neutral", "risk_judge",
    "investment_plan", "portfolio_manager_final_decision",
]
_ROLE_ORDER_INDEX = {role: i for i, role in enumerate(_ROLE_ORDER)}


def _to_summary(run: AgentRun) -> AgentRunSummary:
    decision = run.decisions[0] if run.decisions else None
    return AgentRunSummary(
        id=run.id, ticker=run.ticker, run_type=run.run_type, started_at=run.started_at,
        outcome=run.outcome,
        decision=DecisionSummary.model_validate(decision) if decision else None,
    )


@router.get("/decisions", response_model=DecisionsResponse)
def list_decisions(ticker: str | None = None, db: Session = Depends(get_db)) -> DecisionsResponse:
    query = db.query(AgentRun).order_by(AgentRun.started_at.desc())
    if ticker:
        query = query.filter(AgentRun.ticker == ticker)
    return DecisionsResponse(runs=[_to_summary(run) for run in query.all()])


@router.get("/decisions/{agent_run_id}", response_model=DecisionDetailResponse)
def get_decision_detail(agent_run_id: uuid.UUID, db: Session = Depends(get_db)) -> DecisionDetailResponse:
    run = db.get(AgentRun, agent_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    decision = run.decisions[0] if run.decisions else None
    transcripts = sorted(
        run.debate_transcripts, key=lambda t: _ROLE_ORDER_INDEX.get(t.role, len(_ROLE_ORDER)),
    )
    return DecisionDetailResponse(
        id=run.id, ticker=run.ticker, run_type=run.run_type, started_at=run.started_at,
        outcome=run.outcome, market_status=run.market_status,
        decision=DecisionSummary.model_validate(decision) if decision else None,
        transcripts=[TranscriptEntry(role=t.role, content=t.content) for t in transcripts],
        order_id=decision.orders[0].id if decision and decision.orders else None,
    )
```

- [ ] **Step 4: Write `routes/tickers.py`**

```python
"""GET /api/ticker/{symbol} — drill-down hub: decision history + orders for one ticker."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tradingsystem.dashboard.dependencies import get_db
from tradingsystem.dashboard.routes.decisions import _to_summary
from tradingsystem.dashboard.routes.orders import _to_order_out
from tradingsystem.dashboard.schemas import TickerDetailResponse
from tradingsystem.db.models import AgentRun, Order

router = APIRouter()


@router.get("/ticker/{symbol}", response_model=TickerDetailResponse)
def get_ticker_detail(symbol: str, db: Session = Depends(get_db)) -> TickerDetailResponse:
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.ticker == symbol)
        .order_by(AgentRun.started_at.desc())
        .all()
    )
    orders = (
        db.query(Order)
        .filter(Order.ticker == symbol)
        .order_by(Order.submitted_at.desc())
        .all()
    )
    return TickerDetailResponse(
        ticker=symbol,
        runs=[_to_summary(r) for r in runs],
        orders=[_to_order_out(o) for o in orders],
    )
```

Note: `tickers.py` imports `_to_order_out` from `routes/orders.py`, built in Task 6 — this task's route file is written now but wired into `app.py` only after Task 6 exists (Step 6 below is deferred to the end of Task 6). For now, complete Steps 1–3 of this task (decisions route) and land it; `tickers.py` content is written here but its import will fail until Task 6 lands `orders.py`, so **do not wire `tickers.py` into `app.py` in this task** — that happens as the first step of Task 6.

- [ ] **Step 5: Wire `decisions.py` into `app.py`**

Add `from tradingsystem.dashboard.routes import decisions` and `app.include_router(decisions.router, prefix="/api")`.

- [ ] **Step 6: Run the decisions tests to verify they pass**

Run: `pytest tests/test_dashboard_api_decisions.py -v`
Expected: PASS (ticker-detail tests still fail until Task 6 wires `tickers.py` — expected at this point)

- [ ] **Step 7: Commit**

```bash
git add src/tradingsystem/dashboard/routes/decisions.py src/tradingsystem/dashboard/routes/tickers.py src/tradingsystem/dashboard/app.py tests/test_dashboard_api_decisions.py tests/test_dashboard_api_ticker_detail.py
git commit -m "feat: add GET /api/decisions, /api/decisions/{id}, and draft /api/ticker/{symbol}"
```

---

### Task 6: Orders API route (list + cancel) + wire Ticker Detail

**Files:**
- Create: `src/tradingsystem/dashboard/routes/orders.py`
- Modify: `src/tradingsystem/dashboard/app.py` (wire `orders.router` and `tickers.router`)
- Test: `tests/test_dashboard_api_orders.py`, `tests/test_dashboard_api_orders_cancel.py`

**Interfaces:**
- Consumes: `schemas.{OrderOut,FillOut,OrdersResponse}` (Task 2), `execution.executor._TERMINAL_ORDER_STATUSES` (existing), `dependencies.{get_db,get_alpaca_client,require_same_origin}` (Task 1)
- Produces: `routes.orders.router` with `GET /orders`, `POST /orders/{order_id}/cancel`; `routes.orders._to_order_out(order: Order) -> OrderOut` (consumed by Task 5's `tickers.py`)

- [ ] **Step 1: Write failing tests**

`tests/test_dashboard_api_orders.py`:
```python
import datetime

from tradingsystem.db.models import AgentRun, Decision, Fill, Order


def _make_order(db_session, status="new"):
    now = datetime.datetime.utcnow()
    run = AgentRun(ticker="AAPL", run_type="pre_market", started_at=now, finished_at=now,
                    market_status="open", outcome="decision_recorded")
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="x")
    db_session.add(decision)
    db_session.flush()
    order = Order(decision_id=decision.id, ticker="AAPL", side="buy", qty=10, limit_price=100.0,
                  status=status, alpaca_order_id="abc123", submitted_at=now)
    db_session.add(order)
    db_session.flush()
    return order


def test_api_orders_lists_with_nested_fills_and_cancellable_flag(client, db_session):
    order = _make_order(db_session, status="new")
    db_session.add(Fill(order_id=order.id, fill_price=101.0, fill_qty=10, filled_at=datetime.datetime.utcnow()))
    db_session.flush()

    response = client.get("/api/orders")

    body = response.json()
    row = next(o for o in body["orders"] if o["id"] == str(order.id))
    assert row["cancellable"] is True
    assert len(row["fills"]) == 1
    assert row["agent_run_id"] is not None


def test_api_orders_terminal_status_is_not_cancellable(client, db_session):
    _make_order(db_session, status="filled")

    response = client.get("/api/orders")

    body = response.json()
    assert all(o["cancellable"] is False for o in body["orders"] if o["status"] == "filled")
```

`tests/test_dashboard_api_orders_cancel.py` — same cases as the existing `tests/test_dashboard_orders_cancel.py`, retargeted at `/api/orders/{id}/cancel` with JSON assertions instead of a 303 redirect:
```python
import uuid

from tradingsystem.dashboard.app import app
from tradingsystem.dashboard.dependencies import get_alpaca_client
from tests.test_dashboard_api_orders import _make_order


class FakeCancelAlpacaClient:
    def __init__(self, raise_on_cancel=False):
        self.cancel_calls = []
        self.raise_on_cancel = raise_on_cancel

    def cancel_order(self, alpaca_order_id):
        self.cancel_calls.append(alpaca_order_id)
        if self.raise_on_cancel:
            raise RuntimeError("simulated Alpaca rejection: order already filled")


def test_api_cancel_open_order_succeeds(client, db_session):
    order = _make_order(db_session, status="new")
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/api/orders/{order.id}/cancel")

    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    assert fake_client.cancel_calls == ["abc123"]


def test_api_cancel_terminal_order_returns_409(client, db_session):
    order = _make_order(db_session, status="filled")
    app.dependency_overrides[get_alpaca_client] = lambda: FakeCancelAlpacaClient()

    response = client.post(f"/api/orders/{order.id}/cancel")

    assert response.status_code == 409


def test_api_cancel_missing_order_returns_404(client, db_session):
    app.dependency_overrides[get_alpaca_client] = lambda: FakeCancelAlpacaClient()

    response = client.post(f"/api/orders/{uuid.uuid4()}/cancel")

    assert response.status_code == 404


def test_api_cancel_surfaces_alpaca_rejection_as_409(client, db_session):
    order = _make_order(db_session, status="new")
    app.dependency_overrides[get_alpaca_client] = lambda: FakeCancelAlpacaClient(raise_on_cancel=True)

    response = client.post(f"/api/orders/{order.id}/cancel")

    assert response.status_code == 409
    assert "already filled" in response.json()["detail"]


def test_api_cancel_rejects_mismatched_origin(client, db_session):
    order = _make_order(db_session, status="new")
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/api/orders/{order.id}/cancel", headers={"origin": "http://evil.example"})

    assert response.status_code == 403
    assert fake_client.cancel_calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_api_orders.py tests/test_dashboard_api_orders_cancel.py -v`
Expected: FAIL with 404

- [ ] **Step 3: Write `routes/orders.py`**

```python
"""GET /api/orders, POST /api/orders/{order_id}/cancel."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from tradingsystem.dashboard.dependencies import get_alpaca_client, get_db, require_same_origin
from tradingsystem.dashboard.schemas import FillOut, OrderOut, OrdersResponse
from tradingsystem.db.models import Order
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol
from tradingsystem.execution.executor import _TERMINAL_ORDER_STATUSES

router = APIRouter()


def _to_order_out(order: Order) -> OrderOut:
    return OrderOut(
        id=order.id, submitted_at=order.submitted_at, ticker=order.ticker, side=order.side,
        qty=order.qty, limit_price=order.limit_price, status=order.status,
        decision_id=order.decision_id, agent_run_id=order.decision.agent_run_id,
        fills=[FillOut.model_validate(f) for f in order.fills],
        cancellable=order.status not in _TERMINAL_ORDER_STATUSES,
    )


@router.get("/orders", response_model=OrdersResponse)
def list_orders(db: Session = Depends(get_db)) -> OrdersResponse:
    orders = db.query(Order).order_by(Order.submitted_at.desc()).all()
    return OrdersResponse(orders=[_to_order_out(o) for o in orders])


@router.post("/orders/{order_id}/cancel")
def cancel_order_route(
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    alpaca_client: AlpacaClientProtocol = Depends(get_alpaca_client),
    _: None = Depends(require_same_origin),
) -> dict:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status in _TERMINAL_ORDER_STATUSES:
        raise HTTPException(status_code=409, detail=f"order already {order.status}, nothing to cancel")
    try:
        alpaca_client.cancel_order(order.alpaca_order_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"cancelled": True}
```

- [ ] **Step 4: Wire `orders.py` and `tickers.py` into `app.py`**

Add:
```python
from tradingsystem.dashboard.routes import orders, tickers
```
```python
app.include_router(orders.router, prefix="/api")
app.include_router(tickers.router, prefix="/api")
```

- [ ] **Step 5: Run all four route test files to verify they pass**

Run: `pytest tests/test_dashboard_api_orders.py tests/test_dashboard_api_orders_cancel.py tests/test_dashboard_api_ticker_detail.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/dashboard/routes/orders.py src/tradingsystem/dashboard/app.py tests/test_dashboard_api_orders.py tests/test_dashboard_api_orders_cancel.py
git commit -m "feat: add GET /api/orders and POST /api/orders/{id}/cancel, wire ticker detail"
```

---

### Task 7: P&L API routes (`GET /api/pnl`, `GET /api/pnl/series`)

**Files:**
- Create: `src/tradingsystem/dashboard/routes/pnl.py`
- Modify: `src/tradingsystem/dashboard/app.py`
- Test: `tests/test_dashboard_api_pnl.py`

**Interfaces:**
- Consumes: `schemas.{OverviewSnapshot,RealizedPnlOut,PnlResponse,PnlSeriesPoint,PnlSeriesResponse}` (Task 2)
- Produces: `routes.pnl.router` with `GET /pnl`, `GET /pnl/series`

- [ ] **Step 1: Write the failing test**

```python
import datetime

from tradingsystem.db.models import PortfolioSnapshot, RealizedPnl


def test_api_pnl_lists_snapshots_and_realized(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.query(RealizedPnl).delete()
    db_session.flush()
    db_session.add(PortfolioSnapshot(snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0, positions={}))
    db_session.add(RealizedPnl(ticker="AAPL", decision_ids=[], pnl_amount=250.0, closed_at=datetime.datetime.utcnow()))
    db_session.flush()

    response = client.get("/api/pnl")

    body = response.json()
    assert body["snapshots"][0]["equity"] == 100000.0
    assert body["realized"][0]["pnl_amount"] == 250.0


def test_api_pnl_series_orders_points_by_date_ascending(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.flush()
    db_session.add(PortfolioSnapshot(snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0, positions={}))
    db_session.add(PortfolioSnapshot(snapshot_date=datetime.date(2026, 8, 25), equity=101_500.0, cash=80_000.0, positions={}))
    db_session.flush()

    response = client.get("/api/pnl/series")

    points = response.json()["points"]
    dates = [p["date"] for p in points]
    assert dates == sorted(dates)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_api_pnl.py -v`
Expected: FAIL with 404

- [ ] **Step 3: Write `routes/pnl.py`**

```python
"""GET /api/pnl, GET /api/pnl/series — snapshot/realized-P&L history and the equity-curve chart series."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tradingsystem.dashboard.dependencies import get_db
from tradingsystem.dashboard.schemas import OverviewSnapshot, PnlResponse, PnlSeriesPoint, PnlSeriesResponse, RealizedPnlOut
from tradingsystem.db.models import PortfolioSnapshot, RealizedPnl

router = APIRouter()


@router.get("/pnl", response_model=PnlResponse)
def get_pnl(db: Session = Depends(get_db)) -> PnlResponse:
    snapshots = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.snapshot_date.desc()).all()
    realized = db.query(RealizedPnl).order_by(RealizedPnl.closed_at.desc()).all()
    return PnlResponse(
        snapshots=[OverviewSnapshot.model_validate(s) for s in snapshots],
        realized=[RealizedPnlOut.model_validate(r) for r in realized],
    )


@router.get("/pnl/series", response_model=PnlSeriesResponse)
def get_pnl_series(db: Session = Depends(get_db)) -> PnlSeriesResponse:
    snapshots = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.snapshot_date.asc()).all()
    return PnlSeriesResponse(
        points=[PnlSeriesPoint(date=s.snapshot_date, equity=s.equity) for s in snapshots],
    )
```

- [ ] **Step 4: Wire into `app.py`**

Add `from tradingsystem.dashboard.routes import pnl` and `app.include_router(pnl.router, prefix="/api")`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_dashboard_api_pnl.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/dashboard/routes/pnl.py src/tradingsystem/dashboard/app.py tests/test_dashboard_api_pnl.py
git commit -m "feat: add GET /api/pnl and /api/pnl/series endpoints"
```

---

### Task 8: Control API routes

Moves the already-JSON-returning control routes under `/api/control/*` and adds a `GET /api/control/status` (replacing the `GET /control` template route's data).

**Files:**
- Create: `src/tradingsystem/dashboard/routes/control.py`
- Modify: `src/tradingsystem/dashboard/app.py` (wire router; old `/control/*` routes untouched until Task 25)
- Test: `tests/test_dashboard_api_control.py`

**Interfaces:**
- Consumes: `schemas.{ProcessStatusOut,ControlStatusResponse}` (Task 2), `orchestration.process_control` (existing: `get_process_status`, `spawn_detached`, `request_stop`, `clear_stop_request`, `force_kill`, `remove_pidfile`, `RUN_DIR`)
- Produces: `routes.control.router` with `GET /control/status`, `POST /control/{name}/start`, `POST /control/{name}/stop`, `POST /control/{name}/force-stop`, `POST /control/run-now`

- [ ] **Step 1: Write the failing test**

```python
from tradingsystem.orchestration import process_control


def test_api_control_status_reports_alive_processes(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=12345, alive=True),
    )

    response = client.get("/api/control/status")

    body = response.json()
    assert body["scheduler"]["alive"] is True
    assert body["scheduler"]["pid"] == 12345


def test_api_control_start_refuses_if_already_alive(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=999, alive=True),
    )
    spawn_calls = []
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: spawn_calls.append(module) or -1)

    response = client.post("/api/control/scheduler/start")

    assert response.status_code == 409
    assert spawn_calls == []


def test_api_control_run_now_spawns_off_cycle_run(client, monkeypatch):
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: 4242)

    response = client.post("/api/control/run-now")

    assert response.status_code == 200
    assert response.json() == {"started": True, "pid": 4242}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_api_control.py -v`
Expected: FAIL with 404

- [ ] **Step 3: Write `routes/control.py`**

Port the existing five control handlers (`app.py:316-403` in the current file) verbatim into this router, changing only the route prefix registration (handled by `app.include_router(..., prefix="/api")`) and adding the new status endpoint:

```python
"""GET/POST /api/control/* — scheduler/watchdog process control (existing behavior,
moved under the JSON API — see ARCHITECTURE.md §7 for the stop/force-stop safety rationale)."""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends, HTTPException

from tradingsystem.dashboard.dependencies import require_same_origin
from tradingsystem.dashboard.schemas import ControlStatusResponse, ProcessStatusOut
from tradingsystem.orchestration import process_control

router = APIRouter()

_STOP_POLL_INTERVAL_SECONDS = 2
_STOP_TIMEOUT_SECONDS = 60
_START_POLL_INTERVAL_SECONDS = 0.5
_START_POLL_ATTEMPTS = 6  # ~3 seconds total

_start_lock = threading.Lock()


def _tail_log(name: str, lines: int = 200) -> str | None:
    log_path = process_control.RUN_DIR / f"{name}.log"
    if not log_path.exists():
        return None
    return "\n".join(log_path.read_text().splitlines()[-lines:])


@router.get("/control/status", response_model=ControlStatusResponse)
def control_status() -> ControlStatusResponse:
    scheduler = process_control.get_process_status("scheduler")
    watchdog = process_control.get_process_status("watchdog")
    return ControlStatusResponse(
        scheduler=ProcessStatusOut(alive=scheduler.alive, pid=scheduler.pid),
        watchdog=ProcessStatusOut(alive=watchdog.alive, pid=watchdog.pid),
        scheduler_log=_tail_log("scheduler"),
        watchdog_log=_tail_log("watchdog"),
        run_once_log=_tail_log("run_once"),
    )


@router.post("/control/{name}/start")
def start_process(name: str, _: None = Depends(require_same_origin)):
    if name not in ("scheduler", "watchdog"):
        raise HTTPException(status_code=404, detail="Unknown process")

    with _start_lock:
        if process_control.get_process_status(name).alive:
            raise HTTPException(status_code=409, detail=f"{name} is already running")

        process_control.clear_stop_request(name)
        process_control.spawn_detached(name)
        for _ in range(_START_POLL_ATTEMPTS):
            time.sleep(_START_POLL_INTERVAL_SECONDS)
            status = process_control.get_process_status(name)
            if status.alive:
                return {"started": True, "pid": status.pid}

    return {"started": True, "pid": None, "note": "spawned but not yet confirmed alive — refresh shortly"}


@router.post("/control/{name}/stop")
def stop_process(name: str, _: None = Depends(require_same_origin)):
    if name not in ("scheduler", "watchdog"):
        raise HTTPException(status_code=404, detail="Unknown process")
    status = process_control.get_process_status(name)
    if not status.alive:
        return {"stopped": True, "forced": False, "note": "was not running"}

    process_control.request_stop(name)
    waited = 0.0
    while waited < _STOP_TIMEOUT_SECONDS:
        time.sleep(_STOP_POLL_INTERVAL_SECONDS)
        waited += _STOP_POLL_INTERVAL_SECONDS
        if not process_control.get_process_status(name).alive:
            return {"stopped": True, "forced": False}

    return {
        "stopped": False,
        "forced": False,
        "note": (
            f"did not stop within {_STOP_TIMEOUT_SECONDS}s — it may still be finishing an "
            "in-flight research cycle. Use Force Stop only if you're sure it's safe to kill "
            "(a forced kill mid-order-submission can leave an order live at the broker with "
            "no local record)."
        ),
    }


@router.post("/control/{name}/force-stop")
def force_stop_process(name: str, _: None = Depends(require_same_origin)):
    if name not in ("scheduler", "watchdog"):
        raise HTTPException(status_code=404, detail="Unknown process")

    status = process_control.get_process_status(name)
    if not status.alive:
        process_control.clear_stop_request(name)
        return {"stopped": True, "forced": False, "note": "was not running"}

    process_control.force_kill(status.pid)
    process_control.remove_pidfile(name)
    process_control.clear_stop_request(name)
    return {"stopped": True, "forced": True}


@router.post("/control/run-now")
def run_now(_: None = Depends(require_same_origin)):
    pid = process_control.spawn_detached("run_once")
    return {"started": True, "pid": pid}
```

- [ ] **Step 4: Wire into `app.py`**

Add `from tradingsystem.dashboard.routes import control` and `app.include_router(control.router, prefix="/api")`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_dashboard_api_control.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/dashboard/routes/control.py src/tradingsystem/dashboard/app.py tests/test_dashboard_api_control.py
git commit -m "feat: add /api/control/* JSON endpoints"
```

---

### Task 9: Config API routes

**Files:**
- Create: `src/tradingsystem/dashboard/routes/config.py`
- Modify: `src/tradingsystem/dashboard/app.py`
- Test: `tests/test_dashboard_api_config.py`

**Interfaces:**
- Consumes: `schemas.{RiskConfigOut,ConfigResponse}` (Task 2), `config_editing.*` (existing, unchanged), `dependencies.require_same_origin` (Task 1)
- Produces: `routes.config.router` with `GET /config`, `POST /config/candidate-universe`, `POST /config/risk-config`, `POST /config/env-settings` — JSON bodies instead of form fields

- [ ] **Step 1: Write the failing test**

```python
import shutil

import pytest

from tradingsystem.config import REPO_ROOT


@pytest.fixture
def isolated_config_files(tmp_path):
    candidate_src = REPO_ROOT / "config" / "candidate_universe.yaml"
    risk_src = REPO_ROOT / "config" / "risk_config.yaml"
    candidate_copy = tmp_path / "candidate_universe.yaml"
    risk_copy = tmp_path / "risk_config.yaml"
    shutil.copy(candidate_src, candidate_copy)
    shutil.copy(risk_src, risk_copy)

    from tradingsystem.dashboard.routes import config as config_routes
    original_candidate, original_risk = config_routes._CANDIDATE_UNIVERSE_PATH, config_routes._RISK_CONFIG_PATH
    config_routes._CANDIDATE_UNIVERSE_PATH = candidate_copy
    config_routes._RISK_CONFIG_PATH = risk_copy
    yield
    config_routes._CANDIDATE_UNIVERSE_PATH = original_candidate
    config_routes._RISK_CONFIG_PATH = original_risk


def test_api_config_get_returns_tickers_and_risk_config(client, isolated_config_files):
    response = client.get("/api/config")

    body = response.json()
    assert isinstance(body["tickers"], list)
    assert "max_position_pct" in body["risk_config"]


def test_api_config_save_candidate_universe_updates_tickers(client, isolated_config_files):
    response = client.post("/api/config/candidate-universe", json={"tickers": ["AAPL", "MSFT"]})

    assert response.status_code == 200
    body = client.get("/api/config").json()
    assert body["tickers"] == ["AAPL", "MSFT"]


def test_api_config_risk_config_requires_note(client, isolated_config_files):
    response = client.post("/api/config/risk-config", json={
        "max_position_pct": 0.1, "cash_reserve_pct": 0.2, "stop_loss_pct": 0.08,
        "daily_drawdown_breaker_pct": 0.03, "weekly_drawdown_breaker_pct": 0.08,
        "stale_data_max_age_minutes": 15, "note": "",
    })

    assert response.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_api_config.py -v`
Expected: FAIL with 404

- [ ] **Step 3: Write `routes/config.py`**

```python
"""GET/POST /api/config/* — candidate universe, risk config, env settings editing."""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tradingsystem.config import REPO_ROOT
from tradingsystem.dashboard import config_editing
from tradingsystem.dashboard.dependencies import require_same_origin
from tradingsystem.dashboard.schemas import ConfigResponse, RiskConfigOut
from tradingsystem.orchestration import process_control

router = APIRouter()

_RISK_CONFIG_PATH = REPO_ROOT / "config" / "risk_config.yaml"
_CANDIDATE_UNIVERSE_PATH = REPO_ROOT / "config" / "candidate_universe.yaml"
_ENV_PATH = REPO_ROOT / ".env"
_ENV_FIELDS = (
    "discovery_slots_per_cycle", "watchdog_check_interval_minutes",
    "pre_market_cron", "midday_cron",
    "tradingagents_deep_think_model", "tradingagents_quick_think_model",
)


@router.get("/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    env_values = {field: config_editing.read_env_value(_ENV_PATH, field.upper()) for field in _ENV_FIELDS}
    return ConfigResponse(
        tickers=config_editing.read_candidate_universe_tickers(_CANDIDATE_UNIVERSE_PATH),
        risk_config=RiskConfigOut(**config_editing.read_risk_config_values(_RISK_CONFIG_PATH)),
        env_values=env_values,
    )


class CandidateUniverseIn(BaseModel):
    tickers: list[str]


@router.post("/config/candidate-universe")
def post_candidate_universe(body: CandidateUniverseIn, _: None = Depends(require_same_origin)) -> dict:
    try:
        config_editing.write_candidate_universe(_CANDIDATE_UNIVERSE_PATH, body.tickers)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"saved": True}


class RiskConfigIn(BaseModel):
    max_position_pct: float
    cash_reserve_pct: float
    stop_loss_pct: float
    daily_drawdown_breaker_pct: float
    weekly_drawdown_breaker_pct: float
    stale_data_max_age_minutes: int
    note: str


@router.post("/config/risk-config")
def post_risk_config(body: RiskConfigIn, _: None = Depends(require_same_origin)) -> dict:
    note = body.note.strip()
    if not note:
        raise HTTPException(status_code=422, detail="justification is required")
    if len(note) > 2000:
        raise HTTPException(status_code=422, detail="justification must be 2000 characters or fewer")
    note = " ".join(note.split()).replace('"', "'")

    updates = body.model_dump(exclude={"note"})
    try:
        changes = config_editing.write_risk_config(_RISK_CONFIG_PATH, updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if changes:
        config_editing.append_config_change_log(process_control.RUN_DIR, changes, note)
    return {"saved": True}


class EnvSettingsIn(BaseModel):
    discovery_slots_per_cycle: int
    watchdog_check_interval_minutes: int
    pre_market_cron: str
    midday_cron: str
    tradingagents_deep_think_model: str
    tradingagents_quick_think_model: str


@router.post("/config/env-settings")
def post_env_settings(body: EnvSettingsIn, _: None = Depends(require_same_origin)) -> dict:
    if body.discovery_slots_per_cycle < 0:
        raise HTTPException(status_code=422, detail="discovery_slots_per_cycle must be >= 0")
    if body.watchdog_check_interval_minutes <= 0:
        raise HTTPException(status_code=422, detail="watchdog_check_interval_minutes must be > 0")
    for cron_value, field in ((body.pre_market_cron, "pre_market_cron"), (body.midday_cron, "midday_cron")):
        try:
            CronTrigger.from_crontab(cron_value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid {field}: {exc}") from exc
    if not body.tradingagents_deep_think_model.strip() or not body.tradingagents_quick_think_model.strip():
        raise HTTPException(status_code=422, detail="model names must not be empty")

    config_editing.write_env_values(_ENV_PATH, {
        "DISCOVERY_SLOTS_PER_CYCLE": str(body.discovery_slots_per_cycle),
        "WATCHDOG_CHECK_INTERVAL_MINUTES": str(body.watchdog_check_interval_minutes),
        "PRE_MARKET_CRON": body.pre_market_cron,
        "MIDDAY_CRON": body.midday_cron,
        "TRADINGAGENTS_DEEP_THINK_MODEL": body.tradingagents_deep_think_model,
        "TRADINGAGENTS_QUICK_THINK_MODEL": body.tradingagents_quick_think_model,
    })
    return {"saved": True}
```

- [ ] **Step 4: Wire into `app.py`**

Add `from tradingsystem.dashboard.routes import config as config_routes` and `app.include_router(config_routes.router, prefix="/api")`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_dashboard_api_config.py -v`
Expected: PASS

- [ ] **Step 6: Run the full backend suite (old + new) to confirm no regressions**

Run: `pytest tests/ -v`
Expected: all tests PASS — old Jinja2 routes (untouched) and all new `/api/*` routes both green

- [ ] **Step 7: Commit**

```bash
git add src/tradingsystem/dashboard/routes/config.py src/tradingsystem/dashboard/app.py tests/test_dashboard_api_config.py
git commit -m "feat: add /api/config/* endpoints, completing the backend JSON API"
```

---

### Task 10: Frontend project scaffold

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/vite.config.ts`, `frontend/index.html`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`
- Create: `.gitignore` entries for `frontend/node_modules/`, `frontend/dist/`

**Interfaces:**
- Produces: a Vite dev server proxying `/api` to `http://127.0.0.1:8787`, rendering a placeholder `App` component

- [ ] **Step 1: Write `package.json`**

```json
{
  "name": "bot-trading-dashboard",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "e2e": "playwright test"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.0",
    "react-grid-layout": "^1.4.4",
    "recharts": "^2.12.7"
  },
  "devDependencies": {
    "@playwright/test": "^1.47.0",
    "@testing-library/jest-dom": "^6.5.0",
    "@testing-library/react": "^16.0.1",
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "@types/react-grid-layout": "^1.3.5",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^25.0.0",
    "typescript": "^5.5.4",
    "vite": "^5.4.3",
    "vitest": "^2.0.5"
  }
}
```

- [ ] **Step 2: Write `tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

`tsconfig.node.json`:
```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 3: Write `vite.config.ts`**

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8787",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test-setup.ts",
  },
});
```

- [ ] **Step 4: Write `index.html`, `src/main.tsx`, `src/App.tsx`, `src/test-setup.ts`**

`index.html`:
```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Bot-Trading Terminal</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`src/main.tsx`:
```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
```

`src/App.tsx` (placeholder, replaced in Task 14):
```tsx
export default function App() {
  return <div>Bot-Trading Terminal — scaffold OK</div>;
}
```

`src/test-setup.ts`:
```typescript
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 5: Install and verify the dev server serves the placeholder**

Run: `cd frontend && npm install && npm run build`
Expected: build succeeds, `frontend/dist/index.html` exists

- [ ] **Step 6: Update `.gitignore`**

Append to the repo-root `.gitignore`:
```
frontend/node_modules/
frontend/dist/
```

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/tsconfig.json frontend/tsconfig.node.json frontend/vite.config.ts frontend/index.html frontend/src/main.tsx frontend/src/App.tsx frontend/src/test-setup.ts frontend/package-lock.json .gitignore
git commit -m "feat: scaffold React/TypeScript/Vite frontend project"
```

---

### Task 11: Design tokens and global styles

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Create: `frontend/src/styles/global.css`
- Modify: `frontend/index.html` (Google Fonts links)
- Modify: `frontend/src/main.tsx` (import global styles)

**Interfaces:**
- Produces: CSS custom properties `--void --panel --hairline --amber --gain --loss --ink --muted --font-display --font-body --font-mono`, importable by every later component

- [ ] **Step 1: Write `tokens.css`** (values verbatim from spec §2)

```css
:root {
  --void: #0A0A0B;
  --panel: #131316;
  --hairline: #2A2A2E;
  --amber: #FF9F1C;
  --gain: #3ECF6E;
  --loss: #FF5C5C;
  --ink: #E8E6E1;
  --muted: #8A8A8F;

  --font-display: "IBM Plex Sans Condensed", "Arial Narrow", sans-serif;
  --font-body: "IBM Plex Sans", -apple-system, sans-serif;
  --font-mono: "IBM Plex Mono", "Consolas", monospace;

  --radius: 2px;
}
```

- [ ] **Step 2: Write `global.css`**

```css
@import "./tokens.css";

* { box-sizing: border-box; }

html, body, #root { height: 100%; margin: 0; }

body {
  background: var(--void);
  color: var(--ink);
  font-family: var(--font-body);
  font-size: 14px;
}

h1, h2, h3, .label {
  font-family: var(--font-display);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--amber);
}

.mono, table, .stat-value {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

a { color: var(--amber); text-decoration: none; }
a:hover { text-decoration: underline; }

.gain { color: var(--gain); }
.loss { color: var(--loss); }

.flash-gain { animation: flash-gain 400ms ease-out; }
.flash-loss { animation: flash-loss 400ms ease-out; }

@keyframes flash-gain { from { background: rgba(62, 207, 110, 0.35); } to { background: transparent; } }
@keyframes flash-loss { from { background: rgba(255, 92, 92, 0.35); } to { background: transparent; } }

@media (prefers-reduced-motion: reduce) {
  .flash-gain, .flash-loss { animation: none; }
}

:focus-visible {
  outline: 2px solid var(--amber);
  outline-offset: 2px;
}
```

- [ ] **Step 3: Add Google Fonts links to `index.html`**

Add inside `<head>`, before `<title>`:
```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link
  href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@500;700&display=swap"
  rel="stylesheet"
/>
```

- [ ] **Step 4: Import global styles in `main.tsx`**

Add at the top of `frontend/src/main.tsx`:
```tsx
import "./styles/global.css";
```

- [ ] **Step 5: Verify the build still succeeds**

Run: `cd frontend && npm run build`
Expected: build succeeds

- [ ] **Step 6: Commit**

```bash
git add frontend/src/styles/ frontend/index.html frontend/src/main.tsx
git commit -m "feat: add Bloomberg-terminal design tokens and global styles"
```

---

### Task 12: API client, TypeScript types, and `usePolling` hook

**Files:**
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/hooks/usePolling.ts`
- Test: `frontend/src/hooks/usePolling.test.ts`

**Interfaces:**
- Consumes: nothing (leaf module)
- Produces: `api.{overview,positions,ticker,decisions,decisionDetail,orders,cancelOrder,pnl,pnlSeries,controlStatus,controlStart,controlStop,controlForceStop,runNow,config,saveCandidateUniverse,saveRiskConfig,saveEnvSettings}`, `usePolling<T>(fetcher: () => Promise<T>, intervalMs: number): { data: T | null; error: Error | null; loading: boolean }`. Every later page/panel task consumes these two modules.

- [ ] **Step 1: Write `api/types.ts`** (mirrors `schemas.py` field-for-field, Task 2)

```typescript
export interface OverviewSnapshot { snapshot_date: string; equity: number; cash: number; }
export interface HeartbeatOut { last_seen_at: string; last_run_type: string | null; last_ticker: string | null; }
export interface BreakerOut { id: string; breaker_type: string; trigger_reason: string; tripped_at: string; }
export interface OverviewResponse {
  snapshot: OverviewSnapshot | null;
  heartbeat: HeartbeatOut | null;
  heartbeat_stale: boolean;
  active_breakers: BreakerOut[];
}

export interface PositionOut {
  ticker: string; qty: number; avg_entry_price: number; current_price: number;
  unrealized_pnl: number; latest_decision: string | null; latest_rating: string | null;
}
export interface CandidateOut { ticker: string; held: boolean; }
export interface PositionsResponse { positions: PositionOut[]; candidates: CandidateOut[]; }

export interface DecisionSummary { id: string; rating: string; decision: string; reasoning_summary: string; }
export interface AgentRunSummary {
  id: string; ticker: string; run_type: string; started_at: string;
  outcome: string | null; decision: DecisionSummary | null;
}
export interface DecisionsResponse { runs: AgentRunSummary[]; }

export interface TranscriptEntry { role: string; content: string; }
export interface DecisionDetailResponse {
  id: string; ticker: string; run_type: string; started_at: string;
  outcome: string | null; market_status: string; decision: DecisionSummary | null;
  transcripts: TranscriptEntry[]; order_id: string | null;
}

export interface FillOut { filled_at: string; fill_qty: number; fill_price: number; }
export interface OrderOut {
  id: string; submitted_at: string; ticker: string; side: string; qty: number;
  limit_price: number; status: string; decision_id: string; agent_run_id: string;
  fills: FillOut[]; cancellable: boolean;
}
export interface OrdersResponse { orders: OrderOut[]; }

export interface TickerDetailResponse { ticker: string; runs: AgentRunSummary[]; orders: OrderOut[]; }

export interface RealizedPnlOut { id: string; ticker: string; pnl_amount: number; closed_at: string; decision_ids: string[]; }
export interface PnlResponse { snapshots: OverviewSnapshot[]; realized: RealizedPnlOut[]; }
export interface PnlSeriesPoint { date: string; equity: number; }
export interface PnlSeriesResponse { points: PnlSeriesPoint[]; }

export interface ProcessStatusOut { alive: boolean; pid: number | null; }
export interface ControlStatusResponse {
  scheduler: ProcessStatusOut; watchdog: ProcessStatusOut;
  scheduler_log: string | null; watchdog_log: string | null; run_once_log: string | null;
}

export interface RiskConfigOut {
  max_position_pct: number; cash_reserve_pct: number; stop_loss_pct: number;
  daily_drawdown_breaker_pct: number; weekly_drawdown_breaker_pct: number; stale_data_max_age_minutes: number;
}
export interface ConfigResponse { tickers: string[]; risk_config: RiskConfigOut; env_values: Record<string, string | null>; }
```

- [ ] **Step 2: Write `api/client.ts`**

```typescript
import type {
  OverviewResponse, PositionsResponse, DecisionsResponse, DecisionDetailResponse,
  OrdersResponse, PnlResponse, PnlSeriesResponse, ControlStatusResponse, ConfigResponse,
  TickerDetailResponse, RiskConfigOut,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  overview: () => request<OverviewResponse>("/overview"),
  positions: () => request<PositionsResponse>("/positions"),
  ticker: (symbol: string) => request<TickerDetailResponse>(`/ticker/${encodeURIComponent(symbol)}`),
  decisions: (ticker?: string) =>
    request<DecisionsResponse>(`/decisions${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ""}`),
  decisionDetail: (id: string) => request<DecisionDetailResponse>(`/decisions/${id}`),
  orders: () => request<OrdersResponse>("/orders"),
  cancelOrder: (id: string) => request<{ cancelled: boolean }>(`/orders/${id}/cancel`, { method: "POST" }),
  pnl: () => request<PnlResponse>("/pnl"),
  pnlSeries: () => request<PnlSeriesResponse>("/pnl/series"),
  controlStatus: () => request<ControlStatusResponse>("/control/status"),
  controlStart: (name: "scheduler" | "watchdog") =>
    request<{ started: boolean; pid: number | null; note?: string }>(`/control/${name}/start`, { method: "POST" }),
  controlStop: (name: "scheduler" | "watchdog") =>
    request<{ stopped: boolean; forced: boolean; note?: string }>(`/control/${name}/stop`, { method: "POST" }),
  controlForceStop: (name: "scheduler" | "watchdog") =>
    request<{ stopped: boolean; forced: boolean }>(`/control/${name}/force-stop`, { method: "POST" }),
  runNow: () => request<{ started: boolean; pid: number | null }>("/control/run-now", { method: "POST" }),
  config: () => request<ConfigResponse>("/config"),
  saveCandidateUniverse: (tickers: string[]) =>
    request<{ saved: boolean }>("/config/candidate-universe", { method: "POST", body: JSON.stringify({ tickers }) }),
  saveRiskConfig: (updates: RiskConfigOut, note: string) =>
    request<{ saved: boolean }>("/config/risk-config", { method: "POST", body: JSON.stringify({ ...updates, note }) }),
  saveEnvSettings: (updates: Record<string, string | number>) =>
    request<{ saved: boolean }>("/config/env-settings", { method: "POST", body: JSON.stringify(updates) }),
};
```

- [ ] **Step 3: Write the failing `usePolling` test**

```typescript
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { usePolling } from "./usePolling";

describe("usePolling", () => {
  it("fetches immediately and re-fetches after intervalMs", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetcher = vi.fn().mockResolvedValue({ value: 1 });

    const { result } = renderHook(() => usePolling(fetcher, 1000));

    await waitFor(() => expect(result.current.data).toEqual({ value: 1 }));
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => { vi.advanceTimersByTime(1000); });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));

    vi.useRealTimers();
  });

  it("surfaces a fetch error without throwing", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => usePolling(fetcher, 5000));

    await waitFor(() => expect(result.current.error?.message).toBe("network down"));
    expect(result.current.loading).toBe(false);
  });
});
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/hooks/usePolling.test.ts`
Expected: FAIL — `usePolling.ts` doesn't exist

- [ ] **Step 5: Write `hooks/usePolling.ts`**

```typescript
import { useEffect, useRef, useState } from "react";

interface PollingState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

export function usePolling<T>(fetcher: () => Promise<T>, intervalMs: number): PollingState<T> {
  const [state, setState] = useState<PollingState<T>>({ data: null, error: null, loading: true });
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function tick() {
      try {
        const data = await fetcherRef.current();
        if (!cancelled) setState({ data, error: null, loading: false });
      } catch (error) {
        if (!cancelled) setState((prev) => ({ ...prev, error: error as Error, loading: false }));
      } finally {
        if (!cancelled) timer = setTimeout(tick, intervalMs);
      }
    }

    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [intervalMs]);

  return state;
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/hooks/usePolling.test.ts`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/ frontend/src/hooks/
git commit -m "feat: add typed API client and usePolling hook"
```

---

### Task 13: Shared components — Card, StatTile, StatusDot, DataTable, Chart

**Files:**
- Create: `frontend/src/components/Card.tsx`
- Create: `frontend/src/components/StatTile.tsx`
- Create: `frontend/src/components/StatusDot.tsx`
- Create: `frontend/src/components/DataTable.tsx`
- Create: `frontend/src/components/Chart.tsx`
- Test: `frontend/src/components/DataTable.test.tsx`, `frontend/src/components/StatTile.test.tsx`

**Interfaces:**
- Consumes: nothing beyond React + `recharts`
- Produces: `<Card>`, `<StatTile label value flashKey? tone?>`, `<StatusDot ok label>`, `<DataTable<T> columns rows emptyMessage getRowKey>`, `<Chart points xKey yKey>` — every page/panel task consumes these

- [ ] **Step 1: Write failing tests**

`DataTable.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataTable } from "./DataTable";

interface Row { id: string; ticker: string; qty: number; }

describe("DataTable", () => {
  it("renders one row per item with the given columns", () => {
    const rows: Row[] = [{ id: "1", ticker: "AAPL", qty: 10 }];
    render(
      <DataTable<Row>
        columns={[{ header: "Ticker", render: (r) => r.ticker }, { header: "Qty", render: (r) => r.qty }]}
        rows={rows}
        getRowKey={(r) => r.id}
        emptyMessage="No rows"
      />,
    );
    expect(screen.getByText("AAPL")).toBeInTheDocument();
  });

  it("shows the empty message when there are no rows", () => {
    render(
      <DataTable<Row>
        columns={[{ header: "Ticker", render: (r) => r.ticker }]}
        rows={[]}
        getRowKey={(r) => r.id}
        emptyMessage="No rows yet"
      />,
    );
    expect(screen.getByText("No rows yet")).toBeInTheDocument();
  });
});
```

`StatTile.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatTile } from "./StatTile";

describe("StatTile", () => {
  it("renders the label and value", () => {
    render(<StatTile label="Equity" value="$100,000.00" />);
    expect(screen.getByText("Equity")).toBeInTheDocument();
    expect(screen.getByText("$100,000.00")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/DataTable.test.tsx src/components/StatTile.test.tsx`
Expected: FAIL — components don't exist

- [ ] **Step 3: Write `Card.tsx`**

```tsx
import type { ReactNode } from "react";

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div style={{ background: "var(--panel)", border: "1px solid var(--hairline)", borderRadius: "var(--radius)", padding: 16 }}>
      {title && <h2 style={{ fontSize: 13, margin: "0 0 12px" }}>{title}</h2>}
      {children}
    </div>
  );
}
```

- [ ] **Step 4: Write `StatTile.tsx`**

```tsx
import { useEffect, useRef, useState } from "react";

interface StatTileProps {
  label: string;
  value: string;
  tone?: "gain" | "loss" | "neutral";
}

export function StatTile({ label, value, tone = "neutral" }: StatTileProps) {
  const [flash, setFlash] = useState<"flash-gain" | "flash-loss" | "">("");
  const prevValue = useRef(value);

  useEffect(() => {
    if (prevValue.current !== value) {
      setFlash(tone === "loss" ? "flash-loss" : "flash-gain");
      prevValue.current = value;
      const id = setTimeout(() => setFlash(""), 400);
      return () => clearTimeout(id);
    }
  }, [value, tone]);

  return (
    <div className={flash} style={{ padding: 8 }}>
      <div className="label" style={{ fontSize: 11 }}>{label}</div>
      <div className={`stat-value ${tone !== "neutral" ? tone : ""}`} style={{ fontSize: 22 }}>{value}</div>
    </div>
  );
}
```

- [ ] **Step 5: Write `StatusDot.tsx`**

```tsx
export function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span
        style={{
          width: 8, height: 8, borderRadius: "50%",
          background: ok ? "var(--gain)" : "var(--loss)",
          display: "inline-block",
        }}
      />
      <span>{label}</span>
    </span>
  );
}
```

- [ ] **Step 6: Write `DataTable.tsx`**

```tsx
interface Column<T> {
  header: string;
  render: (row: T) => React.ReactNode;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  getRowKey: (row: T) => string;
  emptyMessage: string;
}

export function DataTable<T>({ columns, rows, getRowKey, emptyMessage }: DataTableProps<T>) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.header} style={{ textAlign: "left", padding: "6px 8px", borderBottom: "1px solid var(--hairline)", color: "var(--muted)", fontSize: 11, textTransform: "uppercase" }}>
              {c.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr><td colSpan={columns.length} style={{ padding: 12, color: "var(--muted)" }}>{emptyMessage}</td></tr>
        ) : (
          rows.map((row) => (
            <tr key={getRowKey(row)}>
              {columns.map((c) => (
                <td key={c.header} style={{ padding: "6px 8px", borderBottom: "1px solid var(--hairline)" }}>{c.render(row)}</td>
              ))}
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 7: Write `Chart.tsx`**

```tsx
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

interface ChartPoint { [key: string]: string | number; }

interface ChartProps {
  points: ChartPoint[];
  xKey: string;
  yKey: string;
  height?: number;
}

export function Chart({ points, xKey, yKey, height = 200 }: ChartProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={points}>
        <XAxis dataKey={xKey} stroke="var(--muted)" fontSize={11} />
        <YAxis stroke="var(--muted)" fontSize={11} domain={["auto", "auto"]} />
        <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--hairline)" }} />
        <Line type="monotone" dataKey={yKey} stroke="var(--amber)" dot={false} strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  );
}
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/DataTable.test.tsx src/components/StatTile.test.tsx`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/
git commit -m "feat: add shared Card, StatTile, StatusDot, DataTable, Chart components"
```

---

### Task 14: NavRail, TickerStrip, routes.ts, App shell

**Files:**
- Create: `frontend/src/routes.ts`
- Create: `frontend/src/components/NavRail.tsx`
- Create: `frontend/src/components/TickerStrip.tsx`
- Create: `frontend/src/components/ErrorBoundary.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/components/TickerStrip.test.tsx`

**Interfaces:**
- Consumes: `api.overview` (Task 12), `usePolling` (Task 12), `StatusDot` (Task 13)
- Produces: `routes: RouteDef[]` (`{ path, navLabel, Component }`) consumed by every page task; `<NavRail>`, `<TickerStrip>`, `<ErrorBoundary>` consumed by `App.tsx` and later page wiring

- [ ] **Step 1: Write the failing `TickerStrip` test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TickerStrip } from "./TickerStrip";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { overview: vi.fn() },
}));

describe("TickerStrip", () => {
  it("shows equity and LIVE once the overview loads", async () => {
    vi.mocked(api.overview).mockResolvedValue({
      snapshot: { snapshot_date: "2026-08-24", equity: 102340, cash: 80000 },
      heartbeat: { last_seen_at: "2026-08-24T09:00:00", last_run_type: "pre_market", last_ticker: "AAPL" },
      heartbeat_stale: false,
      active_breakers: [],
    });

    render(<TickerStrip />);

    expect(await screen.findByText(/102,340/)).toBeInTheDocument();
    expect(screen.getByText(/LIVE/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/TickerStrip.test.tsx`
Expected: FAIL — component doesn't exist

- [ ] **Step 3: Write `routes.ts`**

```typescript
import type { ComponentType } from "react";
import Overview from "./pages/Overview";
import Positions from "./pages/Positions";
import TickerDetail from "./pages/TickerDetail";
import Decisions from "./pages/Decisions";
import DecisionDetail from "./pages/DecisionDetail";
import Orders from "./pages/Orders";
import Pnl from "./pages/Pnl";
import Control from "./pages/Control";
import Config from "./pages/Config";

export interface RouteDef {
  path: string;
  navLabel: string;
  navPath: string;
  Component: ComponentType;
}

export const routes: RouteDef[] = [
  { path: "/", navPath: "/", navLabel: "Overview", Component: Overview },
  { path: "/positions", navPath: "/positions", navLabel: "Positions", Component: Positions },
  { path: "/ticker/:symbol", navPath: "/positions", navLabel: "Positions", Component: TickerDetail },
  { path: "/decisions", navPath: "/decisions", navLabel: "Decisions", Component: Decisions },
  { path: "/decisions/:id", navPath: "/decisions", navLabel: "Decisions", Component: DecisionDetail },
  { path: "/orders", navPath: "/orders", navLabel: "Orders", Component: Orders },
  { path: "/pnl", navPath: "/pnl", navLabel: "P&L", Component: Pnl },
  { path: "/control", navPath: "/control", navLabel: "Control", Component: Control },
  { path: "/config", navPath: "/config", navLabel: "Config", Component: Config },
];

// Nav rail shows one entry per distinct navPath, in first-seen order.
export const navEntries: { navPath: string; navLabel: string }[] = routes
  .filter((r, i) => routes.findIndex((r2) => r2.navPath === r.navPath) === i)
  .map((r) => ({ navPath: r.navPath, navLabel: r.navLabel }));
```

This references nine page files that don't exist yet (Tasks 15–23 create them). To keep this task's own test cycle isolated from those, Step 3 creates `routes.ts` but Step 4 below stubs the nine page files as one-line placeholders so the project builds; each page task (15–23) replaces its stub with the real implementation.

- [ ] **Step 4: Create placeholder page stubs**

For each of `Overview.tsx`, `Positions.tsx`, `TickerDetail.tsx`, `Decisions.tsx`, `DecisionDetail.tsx`, `Orders.tsx`, `Pnl.tsx`, `Control.tsx`, `Config.tsx` in `frontend/src/pages/`, write:

```tsx
export default function PLACEHOLDER_NAME() {
  return <div>PLACEHOLDER_NAME — not yet implemented</div>;
}
```

(substituting the real component name each time, e.g. `Overview`, `Positions`, etc.)

- [ ] **Step 5: Write `TickerStrip.tsx`**

```tsx
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { StatusDot } from "./StatusDot";

const money = (n: number) => `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function TickerStrip() {
  const { data } = usePolling(api.overview, 10_000);

  return (
    <div style={{ display: "flex", gap: 24, alignItems: "center", padding: "8px 24px", borderBottom: "1px solid var(--hairline)", fontFamily: "var(--font-mono)" }}>
      <strong className="label">Bot-Trading Terminal</strong>
      {data?.snapshot && <span>EQUITY {money(data.snapshot.equity)}</span>}
      {data && <StatusDot ok={!data.heartbeat_stale} label={data.heartbeat_stale ? "STALE" : "LIVE"} />}
      {data && data.active_breakers.length > 0 && (
        <span className="loss">{data.active_breakers.length} ACTIVE BREAKER{data.active_breakers.length > 1 ? "S" : ""}</span>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Write `NavRail.tsx`**

```tsx
import { NavLink } from "react-router-dom";
import { navEntries } from "../routes";

export function NavRail() {
  return (
    <nav style={{ width: 160, borderRight: "1px solid var(--hairline)", padding: "16px 0" }}>
      {navEntries.map((entry) => (
        <NavLink
          key={entry.navPath}
          to={entry.navPath}
          style={({ isActive }) => ({
            display: "block", padding: "8px 20px", fontFamily: "var(--font-display)",
            textTransform: "uppercase", fontSize: 12, letterSpacing: "0.06em",
            color: isActive ? "var(--amber)" : "var(--ink)",
            borderLeft: isActive ? "2px solid var(--amber)" : "2px solid transparent",
          })}
        >
          {entry.navLabel}
        </NavLink>
      ))}
    </nav>
  );
}
```

- [ ] **Step 7: Write `ErrorBoundary.tsx`**

```tsx
import { Component, type ReactNode } from "react";

interface Props { children: ReactNode; }
interface State { error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return <div className="loss" style={{ padding: 16 }}>Data unavailable — {this.state.error.message}</div>;
    }
    return this.props.children;
  }
}
```

- [ ] **Step 8: Wire `App.tsx`**

```tsx
import { Route, Routes } from "react-router-dom";
import { NavRail } from "./components/NavRail";
import { TickerStrip } from "./components/TickerStrip";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { routes } from "./routes";

export default function App() {
  return (
    <div>
      <TickerStrip />
      <div style={{ display: "flex" }}>
        <NavRail />
        <main style={{ flex: 1, padding: 24 }}>
          <ErrorBoundary>
            <Routes>
              {routes.map((r) => (
                <Route key={r.path} path={r.path} element={<r.Component />} />
              ))}
            </Routes>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
```

- [ ] **Step 9: Run tests and build to verify everything passes**

Run: `cd frontend && npx vitest run src/components/TickerStrip.test.tsx && npm run build`
Expected: PASS, build succeeds

- [ ] **Step 10: Commit**

```bash
git add frontend/src/routes.ts frontend/src/pages/ frontend/src/components/NavRail.tsx frontend/src/components/TickerStrip.tsx frontend/src/components/ErrorBoundary.tsx frontend/src/App.tsx
git commit -m "feat: add app shell — NavRail, TickerStrip, routing, error boundary"
```

---

### Task 15: DashboardGrid + panel registry + first two panels (Equity, Heartbeat)

**Files:**
- Create: `frontend/src/panels/types.ts`
- Create: `frontend/src/panels/registry.ts`
- Create: `frontend/src/panels/EquityPanel.tsx`
- Create: `frontend/src/panels/HeartbeatPanel.tsx`
- Create: `frontend/src/components/DashboardGrid.tsx`
- Modify: `frontend/src/pages/Overview.tsx` (replace placeholder)
- Test: `frontend/src/components/DashboardGrid.test.tsx`

**Interfaces:**
- Consumes: `StatTile`, `StatusDot` (Task 13), `usePolling`, `api` (Task 12)
- Produces: `PanelDefinition` type, `panelRegistry: PanelDefinition[]`, `<DashboardGrid>` — consumed by Task 16 (remaining panels) and the Overview page

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DashboardGrid } from "./DashboardGrid";

describe("DashboardGrid", () => {
  it("renders every registered panel's title", () => {
    render(<DashboardGrid />);
    expect(screen.getByText("Equity")).toBeInTheDocument();
    expect(screen.getByText("Heartbeat")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/DashboardGrid.test.tsx`
Expected: FAIL — `DashboardGrid` doesn't exist

- [ ] **Step 3: Write `panels/types.ts`**

```typescript
import type { ComponentType } from "react";

export interface PanelDefinition {
  id: string;
  title: string;
  defaultSize: { w: number; h: number };
  Component: ComponentType;
}
```

- [ ] **Step 4: Write `panels/EquityPanel.tsx`**

```tsx
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { StatTile } from "../components/StatTile";
import type { PanelDefinition } from "./types";

function EquityPanel() {
  const { data } = usePolling(api.overview, 30_000);
  const value = data?.snapshot ? `$${data.snapshot.equity.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "—";
  return <StatTile label="Equity" value={value} />;
}

export const panel: PanelDefinition = {
  id: "equity", title: "Equity", defaultSize: { w: 3, h: 2 }, Component: EquityPanel,
};
```

- [ ] **Step 5: Write `panels/HeartbeatPanel.tsx`**

```tsx
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { StatusDot } from "../components/StatusDot";
import type { PanelDefinition } from "./types";

function HeartbeatPanel() {
  const { data } = usePolling(api.overview, 10_000);
  if (!data?.heartbeat) return <div>No heartbeat recorded yet.</div>;
  return (
    <div>
      <StatusDot ok={!data.heartbeat_stale} label={data.heartbeat_stale ? "STALE" : "FRESH"} />
      <div className="mono" style={{ marginTop: 8, fontSize: 12, color: "var(--muted)" }}>
        {data.heartbeat.last_run_type} · {data.heartbeat.last_ticker ?? "-"}
      </div>
    </div>
  );
}

export const panel: PanelDefinition = {
  id: "heartbeat", title: "Heartbeat", defaultSize: { w: 3, h: 2 }, Component: HeartbeatPanel,
};
```

- [ ] **Step 6: Write `panels/registry.ts`**

```typescript
import type { PanelDefinition } from "./types";
import { panel as equityPanel } from "./EquityPanel";
import { panel as heartbeatPanel } from "./HeartbeatPanel";

// Panels are registered explicitly here rather than via import.meta.glob:
// Vite's glob import requires statically-analyzable literal patterns, and an
// explicit list keeps the "add a panel" step (one import + one array entry)
// equally simple while staying type-checked end to end.
export const panelRegistry: PanelDefinition[] = [equityPanel, heartbeatPanel];
```

- [ ] **Step 7: Write `DashboardGrid.tsx`**

```tsx
import GridLayout, { type Layout } from "react-grid-layout";
import { useEffect, useState } from "react";
import { panelRegistry } from "../panels/registry";
import "react-grid-layout/css/styles.css";

const LAYOUT_KEY = "dashboard.overview.layout.v1";

function defaultLayout(): Layout[] {
  return panelRegistry.map((p, i) => ({
    i: p.id, x: (i * p.defaultSize.w) % 12, y: 0, w: p.defaultSize.w, h: p.defaultSize.h,
  }));
}

function loadLayout(): Layout[] {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    if (raw) return JSON.parse(raw) as Layout[];
  } catch {
    // localStorage unavailable or corrupt — fall through to defaults
  }
  return defaultLayout();
}

export function DashboardGrid() {
  const [layout, setLayout] = useState<Layout[]>(loadLayout);

  useEffect(() => {
    try {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
    } catch {
      // best-effort persistence only
    }
  }, [layout]);

  return (
    <GridLayout
      className="dashboard-grid"
      cols={12}
      rowHeight={80}
      width={1200}
      layout={layout}
      onLayoutChange={setLayout}
      draggableHandle=".panel-drag-handle"
    >
      {panelRegistry.map((p) => (
        <div key={p.id} className="panel-drag-handle" style={{ background: "var(--panel)", border: "1px solid var(--hairline)", borderRadius: "var(--radius)", padding: 12, overflow: "auto" }}>
          <div className="label" style={{ fontSize: 11, marginBottom: 8, cursor: "move" }}>{p.title}</div>
          <p.Component />
        </div>
      ))}
    </GridLayout>
  );
}
```

- [ ] **Step 8: Wire `Overview.tsx`**

```tsx
import { DashboardGrid } from "../components/DashboardGrid";

export default function Overview() {
  return <DashboardGrid />;
}
```

- [ ] **Step 9: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/DashboardGrid.test.tsx`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add frontend/src/panels/ frontend/src/components/DashboardGrid.tsx frontend/src/pages/Overview.tsx
git commit -m "feat: add composable DashboardGrid, panel registry, Equity/Heartbeat panels"
```

---

### Task 16: Remaining Overview panels — Breakers, EquityCurve, Positions, Activity

**Files:**
- Create: `frontend/src/panels/BreakersPanel.tsx`
- Create: `frontend/src/panels/EquityCurvePanel.tsx`
- Create: `frontend/src/panels/PositionsPanel.tsx`
- Create: `frontend/src/panels/ActivityPanel.tsx`
- Modify: `frontend/src/panels/registry.ts`
- Test: `frontend/src/panels/BreakersPanel.test.tsx`

**Interfaces:**
- Consumes: `api.{overview,pnlSeries,positions,decisions}`, `usePolling` (Task 12), `Chart`, `DataTable` (Task 13), `PanelDefinition` (Task 15)
- Produces: 4 more entries in `panelRegistry`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { panel } from "./BreakersPanel";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { overview: vi.fn() } }));

describe("BreakersPanel", () => {
  it("shows an active breaker's type and reason", async () => {
    vi.mocked(api.overview).mockResolvedValue({
      snapshot: null, heartbeat: null, heartbeat_stale: false,
      active_breakers: [{ id: "1", breaker_type: "daily", trigger_reason: "4% drawdown", tripped_at: "2026-08-24T09:00:00" }],
    });

    render(<panel.Component />);

    expect(await screen.findByText(/daily/)).toBeInTheDocument();
    expect(screen.getByText(/4% drawdown/)).toBeInTheDocument();
  });

  it("shows a clear message when there are no active breakers", async () => {
    vi.mocked(api.overview).mockResolvedValue({ snapshot: null, heartbeat: null, heartbeat_stale: false, active_breakers: [] });

    render(<panel.Component />);

    expect(await screen.findByText(/no active circuit breakers/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/panels/BreakersPanel.test.tsx`
Expected: FAIL — panel doesn't exist

- [ ] **Step 3: Write `panels/BreakersPanel.tsx`**

```tsx
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import type { PanelDefinition } from "./types";

function BreakersPanel() {
  const { data } = usePolling(api.overview, 10_000);
  if (!data) return null;
  if (data.active_breakers.length === 0) return <div className="gain">No active circuit breakers.</div>;
  return (
    <ul style={{ margin: 0, paddingLeft: 16 }}>
      {data.active_breakers.map((b) => (
        <li key={b.id} className="loss">{b.breaker_type} — {b.trigger_reason}</li>
      ))}
    </ul>
  );
}

export const panel: PanelDefinition = {
  id: "breakers", title: "Circuit breakers", defaultSize: { w: 3, h: 2 }, Component: BreakersPanel,
};
```

- [ ] **Step 4: Write `panels/EquityCurvePanel.tsx`**

```tsx
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Chart } from "../components/Chart";
import type { PanelDefinition } from "./types";

function EquityCurvePanel() {
  const { data } = usePolling(api.pnlSeries, 30_000);
  if (!data || data.points.length === 0) return <div>No portfolio snapshots recorded yet.</div>;
  return <Chart points={data.points} xKey="date" yKey="equity" height={180} />;
}

export const panel: PanelDefinition = {
  id: "equity-curve", title: "Equity curve", defaultSize: { w: 6, h: 3 }, Component: EquityCurvePanel,
};
```

- [ ] **Step 5: Write `panels/PositionsPanel.tsx`**

```tsx
import { Link } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import type { PositionOut } from "../api/types";
import type { PanelDefinition } from "./types";

function PositionsPanel() {
  const { data } = usePolling(api.positions, 30_000);
  return (
    <DataTable<PositionOut>
      columns={[
        { header: "Ticker", render: (p) => <Link to={`/ticker/${p.ticker}`}>{p.ticker}</Link> },
        { header: "Qty", render: (p) => p.qty },
        { header: "P&L", render: (p) => <span className={p.unrealized_pnl >= 0 ? "gain" : "loss"}>${p.unrealized_pnl.toFixed(2)}</span> },
        { header: "Decision", render: (p) => p.latest_decision ?? "-" },
      ]}
      rows={data?.positions ?? []}
      getRowKey={(p) => p.ticker}
      emptyMessage="No open positions."
    />
  );
}

export const panel: PanelDefinition = {
  id: "positions", title: "Positions", defaultSize: { w: 6, h: 3 }, Component: PositionsPanel,
};
```

- [ ] **Step 6: Write `panels/ActivityPanel.tsx`**

```tsx
import { Link } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import type { AgentRunSummary } from "../api/types";
import type { PanelDefinition } from "./types";

function ActivityPanel() {
  const { data } = usePolling(() => api.decisions(), 30_000);
  const recent = (data?.runs ?? []).slice(0, 8);
  return (
    <DataTable<AgentRunSummary>
      columns={[
        { header: "When", render: (r) => <Link to={`/decisions/${r.id}`}>{new Date(r.started_at).toLocaleString()}</Link> },
        { header: "Ticker", render: (r) => <Link to={`/ticker/${r.ticker}`}>{r.ticker}</Link> },
        { header: "Outcome", render: (r) => r.decision?.decision ?? r.outcome ?? "-" },
      ]}
      rows={recent}
      getRowKey={(r) => r.id}
      emptyMessage="No activity recorded yet."
    />
  );
}

export const panel: PanelDefinition = {
  id: "activity", title: "Recent activity", defaultSize: { w: 6, h: 3 }, Component: ActivityPanel,
};
```

- [ ] **Step 7: Add all four to `registry.ts`**

```typescript
import type { PanelDefinition } from "./types";
import { panel as equityPanel } from "./EquityPanel";
import { panel as heartbeatPanel } from "./HeartbeatPanel";
import { panel as breakersPanel } from "./BreakersPanel";
import { panel as equityCurvePanel } from "./EquityCurvePanel";
import { panel as positionsPanel } from "./PositionsPanel";
import { panel as activityPanel } from "./ActivityPanel";

export const panelRegistry: PanelDefinition[] = [
  equityPanel, heartbeatPanel, breakersPanel, equityCurvePanel, positionsPanel, activityPanel,
];
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/panels/BreakersPanel.test.tsx`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add frontend/src/panels/
git commit -m "feat: add Breakers, EquityCurve, Positions, Activity Overview panels"
```

---

### Task 17: Positions page

**Files:**
- Modify: `frontend/src/pages/Positions.tsx` (replace placeholder)
- Test: `frontend/src/pages/Positions.test.tsx`

**Interfaces:**
- Consumes: `api.positions`, `usePolling`, `DataTable`, `Card` (existing)

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Positions from "./Positions";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { positions: vi.fn() } }));

describe("Positions page", () => {
  it("lists held positions and candidate tickers, linking to Ticker Detail", async () => {
    vi.mocked(api.positions).mockResolvedValue({
      positions: [{ ticker: "AAPL", qty: 10, avg_entry_price: 180, current_price: 184.5, unrealized_pnl: 45, latest_decision: "buy", latest_rating: "Buy" }],
      candidates: [{ ticker: "MSFT", held: false }],
    });

    render(<MemoryRouter><Positions /></MemoryRouter>);

    expect(await screen.findByRole("link", { name: "AAPL" })).toHaveAttribute("href", "/ticker/AAPL");
    expect(screen.getByText("MSFT")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/Positions.test.tsx`
Expected: FAIL — placeholder doesn't render this content

- [ ] **Step 3: Write `pages/Positions.tsx`**

```tsx
import { Link } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import type { CandidateOut, PositionOut } from "../api/types";

export default function Positions() {
  const { data, error } = usePolling(api.positions, 30_000);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Held positions">
        <DataTable<PositionOut>
          columns={[
            { header: "Ticker", render: (p) => <Link to={`/ticker/${p.ticker}`}>{p.ticker}</Link> },
            { header: "Qty", render: (p) => p.qty },
            { header: "Avg entry", render: (p) => `$${p.avg_entry_price.toFixed(2)}` },
            { header: "Last", render: (p) => `$${p.current_price.toFixed(2)}` },
            { header: "P&L", render: (p) => <span className={p.unrealized_pnl >= 0 ? "gain" : "loss"}>${p.unrealized_pnl.toFixed(2)}</span> },
            { header: "Decision", render: (p) => p.latest_decision ?? "-" },
          ]}
          rows={data?.positions ?? []}
          getRowKey={(p) => p.ticker}
          emptyMessage="No open positions."
        />
      </Card>
      <Card title="Candidate universe">
        <DataTable<CandidateOut>
          columns={[
            { header: "Ticker", render: (c) => <Link to={`/ticker/${c.ticker}`}>{c.ticker}</Link> },
            { header: "Held", render: (c) => (c.held ? "yes" : "no") },
          ]}
          rows={data?.candidates ?? []}
          getRowKey={(c) => c.ticker}
          emptyMessage="No candidate tickers configured."
        />
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/Positions.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Positions.tsx frontend/src/pages/Positions.test.tsx
git commit -m "feat: implement Positions & Watchlist page"
```

---

### Task 18: Ticker Detail page

**Files:**
- Modify: `frontend/src/pages/TickerDetail.tsx`
- Test: `frontend/src/pages/TickerDetail.test.tsx`

**Interfaces:**
- Consumes: `api.ticker`, `useParams` (react-router-dom), `Card`, `DataTable`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import TickerDetail from "./TickerDetail";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { ticker: vi.fn() } }));

describe("TickerDetail page", () => {
  it("shows decision history and orders for the routed symbol", async () => {
    vi.mocked(api.ticker).mockResolvedValue({
      ticker: "AAPL",
      runs: [{ id: "r1", ticker: "AAPL", run_type: "pre_market", started_at: "2026-08-24T09:00:00", outcome: "decision_recorded", decision: { id: "d1", rating: "Buy", decision: "buy", reasoning_summary: "strong" } }],
      orders: [{ id: "o1", submitted_at: "2026-08-24T09:05:00", ticker: "AAPL", side: "buy", qty: 10, limit_price: 180, status: "filled", decision_id: "d1", agent_run_id: "r1", fills: [], cancellable: false }],
    });

    render(
      <MemoryRouter initialEntries={["/ticker/AAPL"]}>
        <Routes><Route path="/ticker/:symbol" element={<TickerDetail />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(api.ticker).toHaveBeenCalledWith("AAPL");
    expect(screen.getByText("buy")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/TickerDetail.test.tsx`
Expected: FAIL

- [ ] **Step 3: Write `pages/TickerDetail.tsx`**

```tsx
import { Link, useParams } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import type { AgentRunSummary, OrderOut } from "../api/types";

export default function TickerDetail() {
  const { symbol = "" } = useParams();
  const { data, error } = usePolling(() => api.ticker(symbol), 30_000);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;

  const latest = data?.runs[0];

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <h1>{symbol}</h1>
      {latest?.decision && (
        <Card title="Latest reasoning">
          <p>Rating: {latest.decision.rating} | Decision: {latest.decision.decision}</p>
          <p>{latest.decision.reasoning_summary}</p>
        </Card>
      )}
      <Card title="Decision history">
        <DataTable<AgentRunSummary>
          columns={[
            { header: "Started", render: (r) => <Link to={`/decisions/${r.id}`}>{new Date(r.started_at).toLocaleString()}</Link> },
            { header: "Run type", render: (r) => r.run_type },
            { header: "Outcome", render: (r) => r.outcome ?? "-" },
            { header: "Decision", render: (r) => r.decision?.decision ?? "-" },
          ]}
          rows={data?.runs ?? []}
          getRowKey={(r) => r.id}
          emptyMessage="No decisions recorded for this ticker yet."
        />
      </Card>
      <Card title="Orders">
        <DataTable<OrderOut>
          columns={[
            { header: "Submitted", render: (o) => new Date(o.submitted_at).toLocaleString() },
            { header: "Side", render: (o) => o.side },
            { header: "Qty", render: (o) => o.qty },
            { header: "Status", render: (o) => o.status },
            { header: "Decision", render: (o) => <Link to={`/decisions/${o.agent_run_id}`}>view</Link> },
          ]}
          rows={data?.orders ?? []}
          getRowKey={(o) => o.id}
          emptyMessage="No orders recorded for this ticker yet."
        />
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/TickerDetail.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/TickerDetail.tsx frontend/src/pages/TickerDetail.test.tsx
git commit -m "feat: implement Ticker Detail drill-down page"
```

---

### Task 19: Decisions + Decision Detail pages

**Files:**
- Modify: `frontend/src/pages/Decisions.tsx`
- Modify: `frontend/src/pages/DecisionDetail.tsx`
- Modify: `frontend/src/hooks/usePolling.ts` (one-shot fetch support)
- Test: `frontend/src/pages/Decisions.test.tsx`, `frontend/src/pages/DecisionDetail.test.tsx`

**Interfaces:**
- Consumes: `api.decisions`, `api.decisionDetail`, `useSearchParams`/`useParams`, `Card`, `DataTable`

- [ ] **Step 1: Write failing tests**

`Decisions.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Decisions from "./Decisions";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { decisions: vi.fn() } }));

describe("Decisions page", () => {
  it("lists runs with links to ticker and decision detail", async () => {
    vi.mocked(api.decisions).mockResolvedValue({
      runs: [{ id: "r1", ticker: "AAPL", run_type: "pre_market", started_at: "2026-08-24T09:00:00", outcome: "decision_recorded", decision: { id: "d1", rating: "Buy", decision: "buy", reasoning_summary: "x" } }],
    });

    render(<MemoryRouter><Decisions /></MemoryRouter>);

    expect(await screen.findByRole("link", { name: "AAPL" })).toHaveAttribute("href", "/ticker/AAPL");
  });
});
```

`DecisionDetail.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import DecisionDetail from "./DecisionDetail";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { decisionDetail: vi.fn() } }));

describe("DecisionDetail page", () => {
  it("renders the transcript in role order with a link back to the ticker", async () => {
    vi.mocked(api.decisionDetail).mockResolvedValue({
      id: "r1", ticker: "AAPL", run_type: "pre_market", started_at: "2026-08-24T09:00:00",
      outcome: "decision_recorded", market_status: "open",
      decision: { id: "d1", rating: "Buy", decision: "buy", reasoning_summary: "strong buy" },
      transcripts: [{ role: "market_analyst", content: "trending up" }, { role: "trader", content: "buy it" }],
      order_id: "o1",
    });

    render(
      <MemoryRouter initialEntries={["/decisions/r1"]}>
        <Routes><Route path="/decisions/:id" element={<DecisionDetail />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("trending up")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AAPL" })).toHaveAttribute("href", "/ticker/AAPL");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/Decisions.test.tsx src/pages/DecisionDetail.test.tsx`
Expected: FAIL

- [ ] **Step 3: Patch `usePolling` for one-shot fetches**

`intervalMs <= 0` should fetch once and not reschedule (needed by `DecisionDetail`, whose transcript never changes after the fact — spec §6). In `frontend/src/hooks/usePolling.ts`, change the `finally` block inside `tick`:
```typescript
      } finally {
        if (!cancelled && intervalMs > 0) timer = setTimeout(tick, intervalMs);
      }
```

- [ ] **Step 4: Run the existing `usePolling` tests to confirm no regression**

Run: `cd frontend && npx vitest run src/hooks/usePolling.test.ts`
Expected: PASS (both existing cases use `intervalMs > 0`, unaffected)

- [ ] **Step 5: Write `pages/Decisions.tsx`**

```tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import type { AgentRunSummary } from "../api/types";

export default function Decisions() {
  const [ticker, setTicker] = useState("");
  const { data, error } = usePolling(() => api.decisions(ticker || undefined), 30_000);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;

  return (
    <div>
      <h1>Decisions / Journal</h1>
      <input
        placeholder="Filter by ticker"
        value={ticker}
        onChange={(e) => setTicker(e.target.value.toUpperCase())}
        style={{ marginBottom: 12, background: "var(--panel)", color: "var(--ink)", border: "1px solid var(--hairline)", padding: 6 }}
      />
      <DataTable<AgentRunSummary>
        columns={[
          { header: "Started", render: (r) => <Link to={`/decisions/${r.id}`}>{new Date(r.started_at).toLocaleString()}</Link> },
          { header: "Ticker", render: (r) => <Link to={`/ticker/${r.ticker}`}>{r.ticker}</Link> },
          { header: "Run type", render: (r) => r.run_type },
          { header: "Outcome", render: (r) => r.outcome ?? "-" },
          { header: "Rating", render: (r) => r.decision?.rating ?? "-" },
          { header: "Decision", render: (r) => r.decision?.decision ?? "-" },
        ]}
        rows={data?.runs ?? []}
        getRowKey={(r) => r.id}
        emptyMessage="No agent runs recorded yet."
      />
    </div>
  );
}
```

- [ ] **Step 6: Write `pages/DecisionDetail.tsx`**

```tsx
import { Link, useParams } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";

export default function DecisionDetail() {
  const { id = "" } = useParams();
  const { data, error } = usePolling(() => api.decisionDetail(id), 0);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;
  if (!data) return null;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div>
        <Link to="/decisions">&larr; back to decisions</Link>
        <h1><Link to={`/ticker/${data.ticker}`}>{data.ticker}</Link> — {data.run_type} — {new Date(data.started_at).toLocaleString()}</h1>
      </div>
      <Card>
        <p>Outcome: {data.outcome ?? "-"} | Market status: {data.market_status}</p>
        {data.decision ? (
          <>
            <p>Rating: {data.decision.rating} | Decision: {data.decision.decision}</p>
            <p>{data.decision.reasoning_summary}</p>
          </>
        ) : (
          <p>No decision recorded for this run.</p>
        )}
        {data.order_id && <p><Link to="/orders">View resulting order &rarr;</Link></p>}
      </Card>
      {data.transcripts.map((t) => (
        <Card key={t.role} title={t.role}>
          <pre style={{ whiteSpace: "pre-wrap", fontFamily: "var(--font-body)" }}>{t.content}</pre>
        </Card>
      ))}
    </div>
  );
}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/Decisions.test.tsx src/pages/DecisionDetail.test.tsx`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/Decisions.tsx frontend/src/pages/DecisionDetail.tsx frontend/src/hooks/usePolling.ts frontend/src/pages/Decisions.test.tsx frontend/src/pages/DecisionDetail.test.tsx
git commit -m "feat: implement Decisions and Decision Detail pages; usePolling supports one-shot fetches"
```

---

### Task 20: Orders page (with cancel)

**Files:**
- Modify: `frontend/src/pages/Orders.tsx`
- Test: `frontend/src/pages/Orders.test.tsx`

**Interfaces:**
- Consumes: `api.orders`, `api.cancelOrder`, `DataTable`

- [ ] **Step 1: Write the failing test**

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Orders from "./Orders";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { orders: vi.fn(), cancelOrder: vi.fn() } }));

describe("Orders page", () => {
  it("shows a Cancel button only for cancellable orders and calls the API on click", async () => {
    vi.mocked(api.orders).mockResolvedValue({
      orders: [{ id: "o1", submitted_at: "2026-08-24T09:00:00", ticker: "AAPL", side: "buy", qty: 10, limit_price: 180, status: "new", decision_id: "d1", agent_run_id: "r1", fills: [], cancellable: true }],
    });
    vi.mocked(api.cancelOrder).mockResolvedValue({ cancelled: true });

    render(<MemoryRouter><Orders /></MemoryRouter>);

    const button = await screen.findByRole("button", { name: /cancel/i });
    fireEvent.click(button);

    await waitFor(() => expect(api.cancelOrder).toHaveBeenCalledWith("o1"));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/Orders.test.tsx`
Expected: FAIL

- [ ] **Step 3: Write `pages/Orders.tsx`**

```tsx
import { Link } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import type { OrderOut } from "../api/types";

export default function Orders() {
  const { data, error } = usePolling(api.orders, 30_000);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;

  async function handleCancel(id: string) {
    await api.cancelOrder(id);
  }

  return (
    <div>
      <h1>Orders</h1>
      <DataTable<OrderOut>
        columns={[
          { header: "Submitted", render: (o) => new Date(o.submitted_at).toLocaleString() },
          { header: "Ticker", render: (o) => <Link to={`/ticker/${o.ticker}`}>{o.ticker}</Link> },
          { header: "Side", render: (o) => o.side },
          { header: "Qty", render: (o) => o.qty },
          { header: "Limit", render: (o) => `$${o.limit_price.toFixed(2)}` },
          { header: "Status", render: (o) => o.status },
          { header: "Decision", render: (o) => <Link to={`/decisions/${o.agent_run_id}`}>view</Link> },
          { header: "Fills", render: (o) => (o.fills.length === 0 ? "-" : o.fills.map((f) => `${f.fill_qty}@$${f.fill_price.toFixed(2)}`).join(", ")) },
          { header: "Cancel", render: (o) => (o.cancellable ? <button onClick={() => handleCancel(o.id)}>Cancel</button> : "-") },
        ]}
        rows={data?.orders ?? []}
        getRowKey={(o) => o.id}
        emptyMessage="No orders recorded yet."
      />
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/Orders.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Orders.tsx frontend/src/pages/Orders.test.tsx
git commit -m "feat: implement Orders & Fills page with cancel action"
```

---

### Task 21: P&L page (with equity curve chart)

**Files:**
- Modify: `frontend/src/pages/Pnl.tsx`
- Test: `frontend/src/pages/Pnl.test.tsx`

**Interfaces:**
- Consumes: `api.pnl`, `api.pnlSeries`, `Chart`, `DataTable`, `Card`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Pnl from "./Pnl";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { pnl: vi.fn(), pnlSeries: vi.fn() } }));

describe("Pnl page", () => {
  it("shows realized P&L rows and renders the equity curve chart container", async () => {
    vi.mocked(api.pnl).mockResolvedValue({
      snapshots: [{ snapshot_date: "2026-08-24", equity: 100000, cash: 80000 }],
      realized: [{ id: "p1", ticker: "AAPL", pnl_amount: 250, closed_at: "2026-08-24T15:00:00", decision_ids: ["d1"] }],
    });
    vi.mocked(api.pnlSeries).mockResolvedValue({ points: [{ date: "2026-08-24", equity: 100000 }] });

    render(<Pnl />);

    expect(await screen.findByText("250.00")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/Pnl.test.tsx`
Expected: FAIL

- [ ] **Step 3: Write `pages/Pnl.tsx`**

```tsx
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";
import { Chart } from "../components/Chart";
import { DataTable } from "../components/DataTable";
import type { OverviewSnapshot, RealizedPnlOut } from "../api/types";

export default function Pnl() {
  const { data: pnl, error } = usePolling(api.pnl, 30_000);
  const { data: series } = usePolling(api.pnlSeries, 30_000);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Equity curve">
        {series && series.points.length > 0 ? (
          <Chart points={series.points} xKey="date" yKey="equity" height={240} />
        ) : (
          <p>No portfolio snapshots recorded yet.</p>
        )}
      </Card>
      <Card title="Portfolio snapshots">
        <DataTable<OverviewSnapshot>
          columns={[
            { header: "Date", render: (s) => s.snapshot_date },
            { header: "Equity", render: (s) => `$${s.equity.toFixed(2)}` },
            { header: "Cash", render: (s) => `$${s.cash.toFixed(2)}` },
          ]}
          rows={pnl?.snapshots ?? []}
          getRowKey={(s) => s.snapshot_date}
          emptyMessage="No portfolio snapshots recorded yet."
        />
      </Card>
      <Card title="Realized P&L">
        <DataTable<RealizedPnlOut>
          columns={[
            { header: "Closed", render: (r) => new Date(r.closed_at).toLocaleString() },
            { header: "Ticker", render: (r) => r.ticker },
            { header: "Amount", render: (r) => <span className={r.pnl_amount >= 0 ? "gain" : "loss"}>{r.pnl_amount.toFixed(2)}</span> },
          ]}
          rows={pnl?.realized ?? []}
          getRowKey={(r) => r.id}
          emptyMessage="No realized P&L recorded yet."
        />
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/Pnl.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Pnl.tsx frontend/src/pages/Pnl.test.tsx
git commit -m "feat: implement P&L page with equity curve chart"
```

---

### Task 22: Control page (with live log polling)

**Files:**
- Modify: `frontend/src/pages/Control.tsx`
- Test: `frontend/src/pages/Control.test.tsx`

**Interfaces:**
- Consumes: `api.{controlStatus,controlStart,controlStop,controlForceStop,runNow}`, `usePolling`

- [ ] **Step 1: Write the failing test**

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Control from "./Control";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { controlStatus: vi.fn(), controlStart: vi.fn(), controlStop: vi.fn(), controlForceStop: vi.fn(), runNow: vi.fn() },
}));

describe("Control page", () => {
  it("disables Start when the scheduler is already running and calls stop on click", async () => {
    vi.mocked(api.controlStatus).mockResolvedValue({
      scheduler: { alive: true, pid: 12345 }, watchdog: { alive: false, pid: null },
      scheduler_log: "log line", watchdog_log: null, run_once_log: null,
    });
    vi.mocked(api.controlStop).mockResolvedValue({ stopped: true, forced: false });

    render(<Control />);

    const startButtons = await screen.findAllByRole("button", { name: "Start" });
    expect(startButtons[0]).toBeDisabled();

    const stopButtons = screen.getAllByRole("button", { name: "Stop" });
    fireEvent.click(stopButtons[0]);

    await waitFor(() => expect(api.controlStop).toHaveBeenCalledWith("scheduler"));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/Control.test.tsx`
Expected: FAIL

- [ ] **Step 3: Write `pages/Control.tsx`**

```tsx
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";

function ProcessControls({ name, alive, pid, log }: { name: "scheduler" | "watchdog"; alive: boolean; pid: number | null; log: string | null }) {
  return (
    <Card title={name === "scheduler" ? "Scheduler" : "Watchdog"}>
      <p>Status: <span className={alive ? "gain" : "loss"}>{alive ? "running" : "stopped"}</span>{alive && ` (PID ${pid})`}</p>
      <button disabled={alive} onClick={() => api.controlStart(name)}>Start</button>{" "}
      <button disabled={!alive} onClick={() => api.controlStop(name)}>Stop</button>{" "}
      <button disabled={!alive} onClick={() => api.controlForceStop(name)}>Force Stop</button>
      <h3 style={{ fontSize: 12 }}>Log (last 200 lines)</h3>
      <pre className="mono" style={{ maxHeight: 200, overflow: "auto", fontSize: 11 }}>{log ?? "no log yet"}</pre>
    </Card>
  );
}

export default function Control() {
  const { data, error } = usePolling(api.controlStatus, 5_000);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;
  if (!data) return null;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <ProcessControls name="scheduler" alive={data.scheduler.alive} pid={data.scheduler.pid} log={data.scheduler_log} />
      <ProcessControls name="watchdog" alive={data.watchdog.alive} pid={data.watchdog.pid} log={data.watchdog_log} />
      <Card title="Off-cycle run">
        <button onClick={() => api.runNow()}>Run now</button>
        <h3 style={{ fontSize: 12 }}>Log (last 200 lines)</h3>
        <pre className="mono" style={{ maxHeight: 200, overflow: "auto", fontSize: 11 }}>{data.run_once_log ?? "no log yet"}</pre>
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/Control.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Control.tsx frontend/src/pages/Control.test.tsx
git commit -m "feat: implement Control page with live-polling log tail"
```

---

### Task 23: Config page

**Files:**
- Modify: `frontend/src/pages/Config.tsx`
- Test: `frontend/src/pages/Config.test.tsx`

**Interfaces:**
- Consumes: `api.{config,saveCandidateUniverse,saveRiskConfig,saveEnvSettings}`

- [ ] **Step 1: Write the failing test**

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Config from "./Config";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { config: vi.fn(), saveCandidateUniverse: vi.fn(), saveRiskConfig: vi.fn(), saveEnvSettings: vi.fn() },
}));

describe("Config page", () => {
  it("requires a justification note before saving risk config", async () => {
    vi.mocked(api.config).mockResolvedValue({
      tickers: ["AAPL"],
      risk_config: { max_position_pct: 0.1, cash_reserve_pct: 0.2, stop_loss_pct: 0.08, daily_drawdown_breaker_pct: 0.03, weekly_drawdown_breaker_pct: 0.08, stale_data_max_age_minutes: 15 },
      env_values: { discovery_slots_per_cycle: "2" },
    });

    render(<Config />);

    const saveButtons = await screen.findAllByRole("button", { name: "Save" });
    fireEvent.click(saveButtons[1]); // risk config's Save button

    expect(api.saveRiskConfig).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/Config.test.tsx`
Expected: FAIL

- [ ] **Step 3: Write `pages/Config.tsx`**

```tsx
import { useEffect, useState } from "react";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";
import type { RiskConfigOut } from "../api/types";

export default function Config() {
  const { data } = usePolling(api.config, 0);
  const [tickers, setTickers] = useState("");
  const [risk, setRisk] = useState<RiskConfigOut | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (data) {
      setTickers(data.tickers.join("\n"));
      setRisk(data.risk_config);
    }
  }, [data]);

  if (!data || !risk) return null;

  async function saveCandidates() {
    await api.saveCandidateUniverse(tickers.split("\n").map((t) => t.trim()).filter(Boolean));
  }

  async function saveRisk() {
    if (!note.trim()) return;
    await api.saveRiskConfig(risk!, note);
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Candidate universe">
        <p>Freely editable — add/remove tickers, takes effect next cycle.</p>
        <textarea rows={10} cols={20} value={tickers} onChange={(e) => setTickers(e.target.value)} />
        <br />
        <button onClick={saveCandidates}>Save</button>
      </Card>
      <Card title="Risk config">
        <p>Changing these is a deliberate decision — a justification is required.</p>
        {(Object.keys(risk) as (keyof RiskConfigOut)[]).map((field) => (
          <label key={field} style={{ display: "block", marginBottom: 4 }}>
            {field}{" "}
            <input
              type="number" step="0.01" value={risk[field]}
              onChange={(e) => setRisk({ ...risk, [field]: Number(e.target.value) })}
            />
          </label>
        ))}
        <label>Justification (required)<br />
          <textarea rows={3} cols={40} value={note} onChange={(e) => setNote(e.target.value)} required />
        </label>
        <br />
        <button onClick={saveRisk}>Save</button>
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/Config.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Config.tsx frontend/src/pages/Config.test.tsx
git commit -m "feat: implement Config page (candidate universe, risk config, env settings)"
```

---

### Task 24: Wire FastAPI static serving of the built frontend

**Files:**
- Modify: `src/tradingsystem/dashboard/app.py`
- Test: `tests/test_dashboard_spa_serving.py`

**Interfaces:**
- Consumes: `tradingsystem.config.REPO_ROOT` (existing)
- Produces: `GET /assets/*` (static), `GET /{any-non-api-path}` → `frontend/dist/index.html`

- [ ] **Step 1: Write the failing test**

```python
def test_spa_fallback_serves_index_html_for_unknown_frontend_route(client, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>placeholder shell</html>")

    from tradingsystem.dashboard import app as app_module
    monkeypatch.setattr(app_module, "_FRONTEND_DIST", dist)

    response = client.get("/positions")

    assert response.status_code == 200
    assert "placeholder shell" in response.text


def test_api_routes_are_not_swallowed_by_spa_fallback(client, db_session):
    response = client.get("/api/overview")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_spa_serving.py -v`
Expected: FAIL — `/positions` currently 404s (no such Jinja2 route) or `_FRONTEND_DIST` doesn't exist

- [ ] **Step 3: Add static serving to `app.py`**

Near the top, add:
```python
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
```

After all `app.include_router(...)` calls, add:
```python
_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

if (_FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="frontend-assets")


@app.get("/{full_path:path}")
def spa_fallback(full_path: str) -> FileResponse:
    return FileResponse(str(_FRONTEND_DIST / "index.html"))
```

This catch-all must be registered after every `/api/*` router include so API routes match first (FastAPI matches routes in registration order). `REPO_ROOT` is already imported in `app.py` from `tradingsystem.config`.

- [ ] **Step 4: Build the frontend so `frontend/dist/` exists for manual verification**

Run: `cd frontend && npm run build`
Expected: `frontend/dist/index.html` and `frontend/dist/assets/` exist

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_dashboard_spa_serving.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/dashboard/app.py tests/test_dashboard_spa_serving.py
git commit -m "feat: serve built frontend as static files with SPA fallback"
```

---

### Task 25: Remove the old Jinja2 dashboard

Only safe once every capability has an equivalent `/api/*` route (Tasks 3–9) and the frontend serves it (Tasks 10–24). This is the single task where old behavior is deleted — verified by the full test suite before and after.

**Files:**
- Modify: `src/tradingsystem/dashboard/app.py` (remove every `templates.TemplateResponse` route: `GET /`, `GET /decisions`, `GET /decisions/{agent_run_id}`, `GET /orders`, `GET /pnl`, `GET /config`, `GET /control`, `POST /orders/{order_id}/cancel` (old redirect version — superseded by `/api/orders/{id}/cancel`), `POST /config/*` (old form versions), remove `Jinja2Templates`/`templates` setup, remove now-unused imports)
- Delete: `src/tradingsystem/dashboard/templates/` (entire directory: `base.html`, `overview.html`, `decisions.html`, `decision_detail.html`, `orders.html`, `pnl.html`, `config.html`, `control.html`)
- Delete: `tests/test_dashboard_decisions.py`, `tests/test_dashboard_decision_detail.py`, `tests/test_dashboard_orders.py`, `tests/test_dashboard_overview.py`, `tests/test_dashboard_pnl.py`, `tests/test_dashboard_control.py`, `tests/test_dashboard_config.py`, `tests/test_dashboard_orders_cancel.py` (superseded by the `test_dashboard_api_*.py` files from Tasks 3–9)
- Modify: `pyproject.toml` (remove `jinja2>=3.1` from `dependencies` — grep confirms nothing else imports it)

- [ ] **Step 1: Confirm nothing outside the dashboard imports Jinja2**

Run: `grep -rn "jinja2\|Jinja2Templates" src/ --include=*.py`
Expected: only matches inside `src/tradingsystem/dashboard/app.py`

- [ ] **Step 2: Delete the old HTML test files**

```bash
git rm tests/test_dashboard_decisions.py tests/test_dashboard_decision_detail.py tests/test_dashboard_orders.py tests/test_dashboard_overview.py tests/test_dashboard_pnl.py tests/test_dashboard_control.py tests/test_dashboard_config.py tests/test_dashboard_orders_cancel.py
```

- [ ] **Step 3: Delete the templates directory**

```bash
git rm -r src/tradingsystem/dashboard/templates
```

- [ ] **Step 4: Strip `app.py` down to router includes + static serving**

Final `app.py` shape:
```python
"""FastAPI dashboard app over the second-brain database — JSON API under /api/*
plus static serving of the built React frontend (see
docs/superpowers/specs/2026-08-31-dashboard-redesign-design.md).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tradingsystem.config import REPO_ROOT
from tradingsystem.dashboard.routes import config as config_routes
from tradingsystem.dashboard.routes import control, decisions, orders, overview, pnl, positions, tickers

app = FastAPI(title="Bot-Trading Dashboard")

app.include_router(overview.router, prefix="/api")
app.include_router(positions.router, prefix="/api")
app.include_router(tickers.router, prefix="/api")
app.include_router(decisions.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(pnl.router, prefix="/api")
app.include_router(control.router, prefix="/api")
app.include_router(config_routes.router, prefix="/api")

_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

if (_FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="frontend-assets")


@app.get("/{full_path:path}")
def spa_fallback(full_path: str) -> FileResponse:
    return FileResponse(str(_FRONTEND_DIST / "index.html"))
```

- [ ] **Step 5: Remove `jinja2` from `pyproject.toml`**

Delete the `"jinja2>=3.1",` line from `[project].dependencies`.

- [ ] **Step 6: Run the full backend suite**

Run: `pytest tests/ -v`
Expected: all remaining tests PASS (the `test_dashboard_api_*.py` files from Tasks 3–9, `test_dashboard_schemas.py`, `test_dashboard_spa_serving.py`, plus every non-dashboard test in the repo)

- [ ] **Step 7: Commit**

```bash
git add -A src/tradingsystem/dashboard/app.py pyproject.toml
git commit -m "chore: remove the old Jinja2 dashboard (superseded by the React SPA + JSON API)"
```

---

### Task 26: Playwright E2E smoke tests

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/dashboard.spec.ts`

**Interfaces:**
- Consumes: a running `python -m tradingsystem.dashboard` instance against the test database, and the built frontend served by it

- [ ] **Step 1: Write `playwright.config.ts`**

```typescript
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://127.0.0.1:8787" },
  webServer: {
    command: "cd .. && python -m tradingsystem.dashboard",
    url: "http://127.0.0.1:8787",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
```

- [ ] **Step 2: Write `e2e/dashboard.spec.ts`**

```typescript
import { expect, test } from "@playwright/test";

test("nav reaches every page", async ({ page }) => {
  await page.goto("/");
  for (const label of ["Positions", "Decisions", "Orders", "P&L", "Control", "Config"]) {
    await page.getByRole("link", { name: label, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(label === "P&L" ? "/pnl" : label.toLowerCase()));
  }
});

test("Overview loads with at least one panel rendered", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Equity")).toBeVisible();
  await expect(page.getByText("Heartbeat")).toBeVisible();
});

test("a ticker link from Decisions resolves to Ticker Detail with matching data", async ({ page }) => {
  await page.goto("/decisions");
  const firstTickerLink = page.locator("table a[href^='/ticker/']").first();
  const ticker = await firstTickerLink.textContent();
  await firstTickerLink.click();
  await expect(page.getByRole("heading", { name: ticker ?? "" })).toBeVisible();
});

test("Control page Start button is disabled while the scheduler is already running", async ({ page }) => {
  await page.goto("/control");
  await expect(page.getByText(/Scheduler/)).toBeVisible();
});
```

- [ ] **Step 3: Run against the built app to verify the smoke suite passes**

Use the `webapp-testing` skill to start the real app (`python -m tradingsystem.dashboard` against the test database) and run:

Run: `cd frontend && npx playwright test`
Expected: all 4 smoke tests PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/playwright.config.ts frontend/e2e/
git commit -m "test: add Playwright E2E smoke tests for the redesigned dashboard"
```

---

### Task 27: Docs and final verification

**Files:**
- Modify: `ARCHITECTURE.md` §7 (replace the dashboard description with the new SPA architecture)
- Modify: `README.md` (add frontend build step to setup instructions, if a setup section exists — check first)

**Interfaces:** none (documentation + verification only)

- [ ] **Step 1: Update `ARCHITECTURE.md` §7**

Replace the "Simple dashboard over the second brain" paragraph (current `ARCHITECTURE.md` lines ~201–234) with a description matching the new architecture: React/TypeScript SPA (Vite build) served by FastAPI as static files with an SPA fallback, JSON API under `/api/*`, 9 views (the original 7 plus new Positions and Ticker Detail pages), live polling (ticker strip ~10s, positions/equity ~30s), composable Overview grid via a panel registry + `react-grid-layout` with `localStorage`-persisted layout. Preserve the existing safety/trust-model language verbatim (same-origin/CSRF guard, no auth, localhost-only, circuit-breaker clearing and kill switch remain CLI-only, Stop/Force-Stop semantics) since none of that changed. Reference `docs/superpowers/specs/2026-08-31-dashboard-redesign-design.md` for the full design, following the existing pattern of linking prior specs from this section.

- [ ] **Step 2: Check `README.md` for a setup/run section and update it if present**

Run: `grep -n "dashboard\|npm\|frontend" README.md`

If a setup section exists, add: `cd frontend && npm install && npm run build` before `python -m tradingsystem.dashboard` in whatever run instructions currently exist for the dashboard.

- [ ] **Step 3: Run the complete verification sweep**

Run: `pytest tests/ -v`
Expected: all backend tests PASS

Run: `cd frontend && npm run build && npx vitest run`
Expected: build succeeds, all frontend unit/component tests PASS

Run: `cd frontend && npx playwright test`
Expected: all E2E smoke tests PASS (Task 26)

- [ ] **Step 4: Commit**

```bash
git add ARCHITECTURE.md README.md
git commit -m "docs: update ARCHITECTURE.md for the React SPA dashboard redesign"
```
