# Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the read-only web dashboard ARCHITECTURE.md §7 named but never designed — the last piece of the originally-scoped system still unbuilt.

**Architecture:** A single FastAPI app (`dashboard/app.py`) with five GET routes, each opening a short-lived DB session via a `get_db` dependency and rendering a Jinja2 template. No business logic beyond querying and shaping data — everything it reads (agent runs, decisions, transcripts, orders, fills, snapshots, realized P&L, circuit breakers, heartbeat) already exists in `db/models.py`.

**Tech Stack:** Python 3.12, FastAPI + Jinja2 + uvicorn (all new dependencies), SQLAlchemy 2.0 (existing), pytest + `fastapi.testclient.TestClient`.

**Spec:** `docs/superpowers/specs/2026-08-25-dashboard-design.md`

## Global Constraints

- Read-only — no route performs a write (including circuit-breaker clearing, deliberately deferred).
- On-demand only — `python -m tradingsystem.dashboard`, not an always-running process.
- Localhost-only, no authentication — binds to `Settings.dashboard_host`/`dashboard_port` (`127.0.0.1:8787` default), matching every other local-only assumption in this project.
- No new JS/chart dependency — plain HTML tables, inline `<style>` in `base.html`, no CDN, no build step.
- No pagination beyond newest-first ordering — data volume is low (a handful of runs per day).
- Every route opens its own session via `get_db` and closes it before returning — no session shared across requests.
- Money values render via a shared Jinja filter (`{{ value|money }}` → `$100,000.00`), not duplicated per template.

---

### Task 1: Scaffolding — dependencies, `get_db`, base template, overview route

Establishes the whole harness (package, test fixture, base template) and delivers the first real route.

**Files:**
- Modify: `pyproject.toml` (add dependencies)
- Create: `src/tradingsystem/dashboard/__init__.py` (empty)
- Create: `src/tradingsystem/dashboard/app.py`
- Create: `src/tradingsystem/dashboard/templates/base.html`
- Create: `src/tradingsystem/dashboard/templates/overview.html`
- Modify: `tests/conftest.py` (add `client` fixture)
- Test: `tests/test_dashboard_overview.py`

**Interfaces:**
- Produces: `app` (the FastAPI instance), `get_db` (dependency function), `templates` (`Jinja2Templates` instance with a `money` filter registered) — all in `dashboard/app.py`, imported directly by every later task. `client` fixture (in `tests/conftest.py`) — reused by every dashboard test file.

- [ ] **Step 1: Add dependencies**

In `pyproject.toml`, add to the `dependencies` list (after `"apscheduler>=3.10"`):

```toml
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "jinja2>=3.1",
```

Run: `.venv\Scripts\python.exe -m pip install -e ".[dev]"`
Expected: installs `fastapi`, `uvicorn`, `jinja2`, and their dependencies (`starlette`, `pydantic` already present, etc.)

- [ ] **Step 2: Write the failing test**

Create `tests/test_dashboard_overview.py`:

```python
import datetime

from tradingsystem.db.models import CircuitBreakerEvent, PortfolioSnapshot, SchedulerHeartbeat

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def test_overview_empty_state_does_not_500(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "No portfolio snapshot recorded yet." in response.text
    assert "No heartbeat recorded yet" in response.text
    assert "No active circuit breakers." in response.text


def test_overview_shows_snapshot_and_fresh_heartbeat(client, db_session):
    db_session.add(PortfolioSnapshot(
        snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0, positions={},
    ))
    db_session.add(SchedulerHeartbeat(
        component="scheduler", last_seen_at=datetime.datetime.utcnow(),
        last_run_type="pre_market", last_ticker="AAPL",
    ))
    db_session.flush()

    response = client.get("/")

    assert response.status_code == 200
    assert "100,000.00" in response.text
    assert "fresh" in response.text
    assert "AAPL" in response.text


def test_overview_shows_active_circuit_breaker(client, db_session):
    db_session.add(CircuitBreakerEvent(breaker_type="daily", trigger_reason="4% drawdown"))
    db_session.flush()

    response = client.get("/")

    assert response.status_code == 200
    assert "daily" in response.text
    assert "4% drawdown" in response.text
```

Add to `tests/conftest.py` (after the `db_session` fixture):

```python
from fastapi.testclient import TestClient

from tradingsystem.dashboard.app import app, get_db


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_overview.py -v`
Expected: FAIL — collection error, `ModuleNotFoundError: No module named 'tradingsystem.dashboard'` (this breaks the whole suite's collection transiently; that's expected and resolved by Step 5)

- [ ] **Step 4: Write the base template**

Create `src/tradingsystem/dashboard/__init__.py` (empty file).

Create `src/tradingsystem/dashboard/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>{% block title %}Bot-Trading Dashboard{% endblock %}</title>
    <style>
        body { font-family: -apple-system, "Segoe UI", sans-serif; margin: 0; background: #0d1117; color: #c9d1d9; }
        nav { background: #161b22; padding: 12px 24px; border-bottom: 1px solid #30363d; }
        nav a { color: #58a6ff; text-decoration: none; margin-right: 20px; font-weight: 600; }
        nav a:hover { text-decoration: underline; }
        main { padding: 24px; max-width: 1100px; margin: 0 auto; }
        table { border-collapse: collapse; width: 100%; margin-bottom: 24px; }
        th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #30363d; }
        th { color: #8b949e; font-size: 0.85em; text-transform: uppercase; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 16px; margin-bottom: 16px; }
        .stale { color: #f85149; font-weight: 600; }
        .fresh { color: #3fb950; font-weight: 600; }
        .active { color: #f85149; font-weight: 600; }
        .clear { color: #3fb950; font-weight: 600; }
        a.link { color: #58a6ff; }
    </style>
</head>
<body>
    <nav>
        <a href="/">Overview</a>
        <a href="/decisions">Decisions</a>
        <a href="/orders">Orders</a>
        <a href="/pnl">P&amp;L</a>
    </nav>
    <main>
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

Create `src/tradingsystem/dashboard/templates/overview.html`:

```html
{% extends "base.html" %}
{% block title %}Overview — Bot-Trading Dashboard{% endblock %}
{% block content %}
<h1>Overview</h1>

<div class="card">
    <h2>Portfolio</h2>
    {% if snapshot %}
    <p>As of {{ snapshot.snapshot_date }}: Equity {{ snapshot.equity|money }}, Cash {{ snapshot.cash|money }}</p>
    {% else %}
    <p>No portfolio snapshot recorded yet.</p>
    {% endif %}
</div>

<div class="card">
    <h2>Scheduler heartbeat</h2>
    {% if heartbeat %}
    <p class="{{ 'stale' if heartbeat_stale else 'fresh' }}">
        Last seen {{ heartbeat.last_seen_at }} ({{ 'STALE' if heartbeat_stale else 'fresh' }})
        — run_type={{ heartbeat.last_run_type }} ticker={{ heartbeat.last_ticker or '-' }}
    </p>
    {% else %}
    <p>No heartbeat recorded yet — scheduler has never run.</p>
    {% endif %}
</div>

<div class="card">
    <h2>Circuit breakers</h2>
    {% if active_breakers %}
    <ul>
    {% for b in active_breakers %}
        <li class="active">{{ b.breaker_type }} — tripped {{ b.tripped_at }}: {{ b.trigger_reason }}</li>
    {% endfor %}
    </ul>
    {% else %}
    <p class="clear">No active circuit breakers.</p>
    {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: Write the app and the overview route**

Create `src/tradingsystem/dashboard/app.py`:

```python
"""FastAPI dashboard app — read-only web UI over the second-brain database.

ARCHITECTURE.md §7. No write actions, localhost-only, on-demand (python -m
tradingsystem.dashboard). Each route opens its own short-lived session via
get_db and closes it before returning — no session shared across requests.
"""

from __future__ import annotations

import datetime
import pathlib

from fastapi import Depends, FastAPI
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from tradingsystem.db.models import CircuitBreakerEvent, PortfolioSnapshot, SchedulerHeartbeat
from tradingsystem.db.session import make_session_factory

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _money(value) -> str:
    if value is None:
        return "-"
    return f"${float(value):,.2f}"


templates.env.filters["money"] = _money

app = FastAPI(title="Bot-Trading Dashboard")

_session_factory = make_session_factory()


def get_db():
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


HEARTBEAT_STALE_AFTER = datetime.timedelta(hours=12)


@app.get("/")
def overview(request: Request, db: Session = Depends(get_db)):
    snapshot = (
        db.query(PortfolioSnapshot)
        .order_by(PortfolioSnapshot.snapshot_date.desc())
        .first()
    )
    heartbeat = db.get(SchedulerHeartbeat, "scheduler")
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
    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "snapshot": snapshot,
            "heartbeat": heartbeat,
            "heartbeat_stale": heartbeat_stale,
            "active_breakers": active_breakers,
        },
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_overview.py -v`
Expected: all 3 tests PASS

- [ ] **Step 7: Run the full suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/tradingsystem/dashboard/__init__.py src/tradingsystem/dashboard/app.py src/tradingsystem/dashboard/templates/base.html src/tradingsystem/dashboard/templates/overview.html tests/conftest.py tests/test_dashboard_overview.py
git commit -m "Add dashboard scaffolding and overview route"
```

---

### Task 2: Decisions list route

**Files:**
- Modify: `src/tradingsystem/dashboard/app.py`
- Create: `src/tradingsystem/dashboard/templates/decisions.html`
- Test: `tests/test_dashboard_decisions.py`

**Interfaces:**
- Consumes: `app`, `get_db`, `templates` (Task 1)
- Produces: nothing new consumed by later tasks — this route is a leaf.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_decisions.py`:

```python
import datetime

from tradingsystem.db.models import AgentRun, Decision

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def test_decisions_list_empty_state_does_not_500(client):
    response = client.get("/decisions")

    assert response.status_code == 200
    assert "No agent runs recorded yet." in response.text


def test_decisions_list_shows_runs(client, db_session):
    run = AgentRun(
        ticker="AAPL", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="decision_recorded",
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="test reasoning"))
    db_session.flush()

    response = client.get("/decisions")

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "Buy" in response.text


def test_decisions_list_filters_by_ticker(client, db_session):
    db_session.add(AgentRun(
        ticker="AAPL", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="market_closed",
    ))
    db_session.add(AgentRun(
        ticker="MSFT", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="market_closed",
    ))
    db_session.flush()

    response = client.get("/decisions?ticker=AAPL")

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "MSFT" not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_decisions.py -v`
Expected: FAIL with `404 Not Found` (no `/decisions` route yet) — assertions on status_code/content fail

- [ ] **Step 3: Write the template**

Create `src/tradingsystem/dashboard/templates/decisions.html`:

```html
{% extends "base.html" %}
{% block title %}Decisions — Bot-Trading Dashboard{% endblock %}
{% block content %}
<h1>Decisions / Journal</h1>

<form method="get" action="/decisions">
    <label>Ticker: <input type="text" name="ticker" value="{{ ticker or '' }}"></label>
    <button type="submit">Filter</button>
</form>

<table>
    <thead>
        <tr><th>Started</th><th>Ticker</th><th>Run type</th><th>Outcome</th><th>Rating</th><th>Decision</th></tr>
    </thead>
    <tbody>
        {% for run, decision in runs %}
        <tr>
            <td><a class="link" href="/decisions/{{ run.id }}">{{ run.started_at }}</a></td>
            <td>{{ run.ticker }}</td>
            <td>{{ run.run_type }}</td>
            <td>{{ run.outcome }}</td>
            <td>{{ decision.rating if decision else '-' }}</td>
            <td>{{ decision.decision if decision else '-' }}</td>
        </tr>
        {% else %}
        <tr><td colspan="6">No agent runs recorded yet.</td></tr>
        {% endfor %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: Add the route**

In `src/tradingsystem/dashboard/app.py`, change the import line:

```python
from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, Decision, PortfolioSnapshot, SchedulerHeartbeat
```

Add at the end of the file:

```python
@app.get("/decisions")
def decisions_list(request: Request, ticker: str | None = None, db: Session = Depends(get_db)):
    query = db.query(AgentRun).order_by(AgentRun.started_at.desc())
    if ticker:
        query = query.filter(AgentRun.ticker == ticker)
    runs = query.all()
    rows = [(run, run.decisions[0] if run.decisions else None) for run in runs]
    return templates.TemplateResponse(request, "decisions.html", {"runs": rows, "ticker": ticker})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_decisions.py -v`
Expected: all 3 tests PASS

- [ ] **Step 6: Run the full suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/tradingsystem/dashboard/app.py src/tradingsystem/dashboard/templates/decisions.html tests/test_dashboard_decisions.py
git commit -m "Add dashboard decisions/journal list route"
```

---

### Task 3: Decision detail route

**Files:**
- Modify: `src/tradingsystem/dashboard/app.py`
- Create: `src/tradingsystem/dashboard/templates/decision_detail.html`
- Test: `tests/test_dashboard_decision_detail.py`

**Interfaces:**
- Consumes: `app`, `get_db`, `templates` (Task 1)
- Produces: nothing new consumed by later tasks — this route is a leaf.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_decision_detail.py`:

```python
import datetime
import uuid

from tradingsystem.db.models import AgentRun, DebateTranscript, Decision

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def test_decision_detail_404_for_missing_run(client):
    response = client.get(f"/decisions/{uuid.uuid4()}")

    assert response.status_code == 404


def test_decision_detail_shows_decision_and_transcripts_in_role_order(client, db_session):
    run = AgentRun(
        ticker="AAPL", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="decision_recorded",
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="strong fundamentals"))
    db_session.add(DebateTranscript(agent_run_id=run.id, role="trader", content="trader says buy"))
    db_session.add(DebateTranscript(agent_run_id=run.id, role="market_analyst", content="market looks strong"))
    db_session.flush()

    response = client.get(f"/decisions/{run.id}")

    assert response.status_code == 200
    body = response.text
    assert "strong fundamentals" in body
    assert body.index("market looks strong") < body.index("trader says buy")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_decision_detail.py -v`
Expected: FAIL with `404 Not Found` for both tests (no `/decisions/{agent_run_id}` route yet), or the second test failing because the route doesn't exist at all

- [ ] **Step 3: Write the template**

Create `src/tradingsystem/dashboard/templates/decision_detail.html`:

```html
{% extends "base.html" %}
{% block title %}Decision detail — Bot-Trading Dashboard{% endblock %}
{% block content %}
<h1>{{ run.ticker }} — {{ run.run_type }} — {{ run.started_at }}</h1>
<p><a class="link" href="/decisions">&larr; back to decisions</a></p>

<div class="card">
    <p>Outcome: {{ run.outcome }} | Market status: {{ run.market_status }}</p>
    {% if decision %}
    <p>Rating: {{ decision.rating }} | Decision: {{ decision.decision }}</p>
    <p>{{ decision.reasoning_summary }}</p>
    {% else %}
    <p>No decision recorded for this run.</p>
    {% endif %}
</div>

{% for role, content in transcripts %}
<div class="card">
    <h3>{{ role }}</h3>
    <pre style="white-space: pre-wrap;">{{ content }}</pre>
</div>
{% endfor %}
{% endblock %}
```

- [ ] **Step 4: Add the route**

In `src/tradingsystem/dashboard/app.py`, change the import lines to:

```python
import uuid
```

(add near the top, alongside `import datetime` / `import pathlib`)

```python
from fastapi import Depends, FastAPI, HTTPException
```

```python
from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, DebateTranscript, Decision, PortfolioSnapshot, SchedulerHeartbeat
```

Add at the end of the file:

```python
_ROLE_ORDER = [
    "market_analyst", "sentiment_analyst", "news_analyst", "fundamentals_analyst",
    "bull_researcher", "bear_researcher", "research_manager_judge", "trader",
    "risk_aggressive", "risk_conservative", "risk_neutral", "risk_judge",
    "investment_plan", "portfolio_manager_final_decision",
]
_ROLE_ORDER_INDEX = {role: i for i, role in enumerate(_ROLE_ORDER)}


@app.get("/decisions/{agent_run_id}")
def decision_detail(request: Request, agent_run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = db.get(AgentRun, agent_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    decision = run.decisions[0] if run.decisions else None
    transcripts = sorted(
        run.debate_transcripts, key=lambda t: _ROLE_ORDER_INDEX.get(t.role, len(_ROLE_ORDER))
    )
    rows = [(t.role, t.content) for t in transcripts]
    return templates.TemplateResponse(
        request, "decision_detail.html", {"run": run, "decision": decision, "transcripts": rows}
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_decision_detail.py -v`
Expected: both tests PASS

- [ ] **Step 6: Run the full suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/tradingsystem/dashboard/app.py src/tradingsystem/dashboard/templates/decision_detail.html tests/test_dashboard_decision_detail.py
git commit -m "Add dashboard decision detail route with ordered debate transcript"
```

---

### Task 4: Orders route

**Files:**
- Modify: `src/tradingsystem/dashboard/app.py`
- Create: `src/tradingsystem/dashboard/templates/orders.html`
- Test: `tests/test_dashboard_orders.py`

**Interfaces:**
- Consumes: `app`, `get_db`, `templates` (Task 1)
- Produces: nothing new consumed by later tasks — this route is a leaf.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_orders.py`:

```python
import datetime

from tradingsystem.db.models import AgentRun, Decision, Fill, Order

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def test_orders_list_empty_state_does_not_500(client):
    response = client.get("/orders")

    assert response.status_code == 200
    assert "No orders recorded yet." in response.text


def test_orders_list_shows_order_and_fill(client, db_session):
    run = AgentRun(
        ticker="AAPL", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="decision_recorded",
    )
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="test")
    db_session.add(decision)
    db_session.flush()
    order = Order(
        decision_id=decision.id, ticker="AAPL", side="buy", qty=10, limit_price=150.0,
        status="filled", alpaca_order_id="abc123", submitted_at=NOW,
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(Fill(order_id=order.id, fill_price=149.5, fill_qty=10, filled_at=NOW))
    db_session.flush()

    response = client.get("/orders")

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "filled" in response.text
    assert "149.50" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_orders.py -v`
Expected: FAIL with `404 Not Found` (no `/orders` route yet)

- [ ] **Step 3: Write the template**

Create `src/tradingsystem/dashboard/templates/orders.html`:

```html
{% extends "base.html" %}
{% block title %}Orders — Bot-Trading Dashboard{% endblock %}
{% block content %}
<h1>Orders</h1>

<table>
    <thead>
        <tr><th>Submitted</th><th>Ticker</th><th>Side</th><th>Qty</th><th>Limit</th><th>Status</th><th>Decision</th><th>Fills</th></tr>
    </thead>
    <tbody>
        {% for order in orders %}
        <tr>
            <td>{{ order.submitted_at }}</td>
            <td>{{ order.ticker }}</td>
            <td>{{ order.side }}</td>
            <td>{{ order.qty }}</td>
            <td>{{ order.limit_price|money }}</td>
            <td>{{ order.status }}</td>
            <td><a class="link" href="/decisions/{{ order.decision.agent_run_id }}">view</a></td>
            <td>
                {% if order.fills %}
                <table>
                    {% for fill in order.fills %}
                    <tr><td>{{ fill.filled_at }}</td><td>{{ fill.fill_qty }} @ {{ fill.fill_price|money }}</td></tr>
                    {% endfor %}
                </table>
                {% else %}
                -
                {% endif %}
            </td>
        </tr>
        {% else %}
        <tr><td colspan="8">No orders recorded yet.</td></tr>
        {% endfor %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: Add the route**

In `src/tradingsystem/dashboard/app.py`, change the import line to:

```python
from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, DebateTranscript, Decision, Order, PortfolioSnapshot, SchedulerHeartbeat
```

Add at the end of the file:

```python
@app.get("/orders")
def orders_list(request: Request, db: Session = Depends(get_db)):
    orders = db.query(Order).order_by(Order.submitted_at.desc()).all()
    return templates.TemplateResponse(request, "orders.html", {"orders": orders})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_orders.py -v`
Expected: both tests PASS

- [ ] **Step 6: Run the full suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/tradingsystem/dashboard/app.py src/tradingsystem/dashboard/templates/orders.html tests/test_dashboard_orders.py
git commit -m "Add dashboard orders route with nested fills"
```

---

### Task 5: P&L route + `__main__.py` entry point

**Files:**
- Modify: `src/tradingsystem/dashboard/app.py`
- Create: `src/tradingsystem/dashboard/templates/pnl.html`
- Create: `src/tradingsystem/dashboard/__main__.py`
- Test: `tests/test_dashboard_pnl.py`

**Interfaces:**
- Consumes: `app`, `get_db`, `templates` (Task 1)
- Produces: `python -m tradingsystem.dashboard` as the full run command — the plan's final deliverable.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_pnl.py`:

```python
import datetime

from tradingsystem.db.models import PortfolioSnapshot, RealizedPnl

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def test_pnl_empty_state_does_not_500(client):
    response = client.get("/pnl")

    assert response.status_code == 200
    assert "No portfolio snapshots recorded yet." in response.text
    assert "No realized P&amp;L recorded yet." in response.text


def test_pnl_shows_snapshots_and_realized(client, db_session):
    db_session.add(PortfolioSnapshot(
        snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0, positions={},
    ))
    db_session.flush()
    db_session.add(RealizedPnl(ticker="AAPL", decision_ids=[], pnl_amount=250.0, closed_at=NOW))
    db_session.flush()

    response = client.get("/pnl")

    assert response.status_code == 200
    assert "100,000.00" in response.text
    assert "250.00" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_pnl.py -v`
Expected: FAIL with `404 Not Found` (no `/pnl` route yet)

- [ ] **Step 3: Write the template**

Create `src/tradingsystem/dashboard/templates/pnl.html`:

```html
{% extends "base.html" %}
{% block title %}P&amp;L — Bot-Trading Dashboard{% endblock %}
{% block content %}
<h1>Portfolio snapshots</h1>
<table>
    <thead><tr><th>Date</th><th>Equity</th><th>Cash</th></tr></thead>
    <tbody>
        {% for s in snapshots %}
        <tr><td>{{ s.snapshot_date }}</td><td>{{ s.equity|money }}</td><td>{{ s.cash|money }}</td></tr>
        {% else %}
        <tr><td colspan="3">No portfolio snapshots recorded yet.</td></tr>
        {% endfor %}
    </tbody>
</table>

<h1>Realized P&amp;L</h1>
<table>
    <thead><tr><th>Closed</th><th>Ticker</th><th>Amount</th></tr></thead>
    <tbody>
        {% for p in realized %}
        <tr><td>{{ p.closed_at }}</td><td>{{ p.ticker }}</td><td>{{ p.pnl_amount|money }}</td></tr>
        {% else %}
        <tr><td colspan="3">No realized P&amp;L recorded yet.</td></tr>
        {% endfor %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: Add the route and the entry point**

In `src/tradingsystem/dashboard/app.py`, change the import line to its final form:

```python
from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, DebateTranscript, Decision, Order, PortfolioSnapshot, RealizedPnl, SchedulerHeartbeat
```

Add at the end of the file:

```python
@app.get("/pnl")
def pnl(request: Request, db: Session = Depends(get_db)):
    snapshots = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.snapshot_date.desc()).all()
    realized = db.query(RealizedPnl).order_by(RealizedPnl.closed_at.desc()).all()
    return templates.TemplateResponse(request, "pnl.html", {"snapshots": snapshots, "realized": realized})
```

Create `src/tradingsystem/dashboard/__main__.py`:

```python
"""Entry point: python -m tradingsystem.dashboard

Starts the read-only dashboard on Settings.dashboard_host/dashboard_port.
"""

import uvicorn

from tradingsystem.config import Settings
from tradingsystem.dashboard.app import app

if __name__ == "__main__":
    settings = Settings()
    uvicorn.run(app, host=settings.dashboard_host, port=settings.dashboard_port)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_pnl.py -v`
Expected: both tests PASS

- [ ] **Step 6: Run the full suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests PASS (35 dashboard-related tests across the 5 test files, plus all pre-existing tests)

- [ ] **Step 7: Manual end-to-end verification**

Run (from repo root, in a way that doesn't block — e.g. a separate terminal, or background with a short timeout since this is a one-off check, not a persistent process for this step):

```bash
.venv\Scripts\python.exe -m tradingsystem.dashboard
```

Expected: starts without error, logs something like `Uvicorn running on http://127.0.0.1:8787`. In a second terminal, run `curl http://127.0.0.1:8787/` and confirm it returns HTML containing `Overview`. Stop the process (Ctrl+C) once confirmed — this is a one-off check, not meant to be left running (the plan's Global Constraints say on-demand only; leaving it running is a separate decision for after this plan is done).

- [ ] **Step 8: Commit**

```bash
git add src/tradingsystem/dashboard/app.py src/tradingsystem/dashboard/templates/pnl.html src/tradingsystem/dashboard/__main__.py tests/test_dashboard_pnl.py
git commit -m "Add dashboard P&L route and __main__ entry point"
```

---

## Self-Review

**Spec coverage:**
- §1 scope (read-only, on-demand, localhost, no charts, no pagination) → enforced throughout; no route ever writes, `__main__.py` is the only entry point, no JS/chart dependency anywhere, all queries are unbounded `.all()` ordered newest-first with no `LIMIT`/offset params.
- §2 module layout (`app.py`, `templates/`, `__main__.py`) → Tasks 1–5 build exactly this layout, nothing else.
- §3 all five routes (`/`, `/decisions`, `/decisions/{id}`, `/orders`, `/pnl`) → one per task (Task 1 covers `/`), each with the exact query/filter/ordering/404 behavior the spec specifies, including the debate-transcript role ordering matching `runner.py`'s `_persist_debate_transcript` sequence.
- §4 templates (`base.html` shell + `money` filter, not duplicated per template) → Task 1 defines both once; every later template extends `base.html` and uses `|money`, none redefines formatting.
- §5 testing (`TestClient` + `db_session` override, one happy-path test per route, plus empty-state and 404/filter edge cases) → every task's test file includes the happy path; empty-state tests appear in Tasks 1, 2, 4, 5; the 404 case is in Task 3; the ticker-filter case is in Task 2.
- §6 exclusions (no write actions, no auth, no charts, no pagination, no auto-refresh, no non-localhost hosting) → nothing in any task adds any of these.

**Placeholder scan:** No TBD/TODO. Every step has runnable code, not a description of code.

**Type consistency:** `get_db()`'s yielded `Session` type matches every route's `db: Session = Depends(get_db)` parameter across all five tasks. `templates.TemplateResponse(request, name, context)` call signature is identical in every route. The `client` fixture (Task 1) is consumed identically by every subsequent test file without redefinition. `_ROLE_ORDER`/`_ROLE_ORDER_INDEX` (Task 3) are used only within that task's route — no other task depends on them.
