# Dashboard — Design Spec

Status: **Approved by user 2026-08-25. Ready for implementation planning.**

This spec covers the dashboard ARCHITECTURE.md §7 named but never designed:
a local, read-only web UI over the second-brain database. It's the last
piece of the originally-scoped system still unbuilt (decision engine, risk
validation, execution, orchestration, and Discord alerting are all live as
of 2026-08-25).

---

## 1. Scope

Read-only. No write actions (including circuit-breaker clearing — a known
gap, deliberately deferred to a future increment, not this one). Started
on demand (`python -m tradingsystem.dashboard`), not an always-running
process alongside the scheduler. Serves `127.0.0.1:8787` by default
(`Settings.dashboard_host`/`dashboard_port`, already reserved, unused until
now).

Out of scope: authentication (binds to localhost only, single operator,
matches every other local-only assumption already made in this project),
any chart library beyond what can be done with plain HTML/CSS (no new JS
dependency), pagination beyond a simple newest-first LIMIT (data volume is
low — a handful of runs per day).

---

## 2. Module layout

New package `src/tradingsystem/dashboard/`:

- **`app.py`** — FastAPI app instance and all five routes. No business
  logic beyond querying and shaping data for templates — matches
  `orchestration/cycle.py`'s existing "thin composition, real logic lives
  in the modules it calls" pattern, except here there's no logic to call
  into; it's read-only reporting over models that already exist.
- **`templates/`** — Jinja2 templates, one per view, plus a shared
  `base.html` for consistent layout/styling (plain CSS, no framework).
- **`__main__.py`** — `uvicorn.run(app, host=settings.dashboard_host,
  port=settings.dashboard_port)`, so `python -m tradingsystem.dashboard`
  is the whole invocation, consistent with `scheduler.py`'s
  `python -m tradingsystem.orchestration.scheduler` entry point pattern.

## 3. Routes

All GET, all read-only, each opens a session via the existing
`make_session_factory()` and closes it before returning:

- **`GET /`** — overview: latest `PortfolioSnapshot` (by `snapshot_date`
  descending), latest `SchedulerHeartbeat` row (flag stale if
  `now - last_seen_at` exceeds some threshold — reuse the twice-daily cron
  cadence to set a sane default, e.g. 12 hours), and the currently-active
  `CircuitBreakerEvent` rows (`cleared_at IS NULL`) per type, if any.
- **`GET /decisions?ticker=<optional>`** — `AgentRun` rows newest-first
  (by `started_at`), optionally filtered by ticker via query param. Shows
  ticker, run_type, outcome, and (via the joined `Decision`, if one exists)
  rating/decision. This is also the "journal" view — skip-outcome rows
  (`market_closed`, `circuit_breaker_active`) appear here too, not just
  trades, matching ARCHITECTURE.md §2's "every run logs a journal entry,
  including no-action days."
- **`GET /decisions/{agent_run_id}`** — one `AgentRun`'s full detail: its
  `Decision` (if any) and every `DebateTranscript` row for that run,
  ordered by role in the same sequence `runner.py`'s
  `_persist_debate_transcript` writes them (market/sentiment/news/
  fundamentals analysts → bull/bear → judge → trader → risk
  aggressive/conservative/neutral → risk judge → investment plan →
  portfolio manager). 404 if the `agent_run_id` doesn't exist.
- **`GET /orders`** — `Order` rows newest-first (by `submitted_at`), each
  with its `Fill` rows nested underneath (a small inline sub-table), and a
  link back to the originating `Decision`/`AgentRun`.
- **`GET /pnl`** — `PortfolioSnapshot` rows ordered by date (a simple HTML
  table of date/equity/cash — a chart is explicitly out of scope per §1)
  plus `RealizedPnl` rows newest-first.

## 4. Templates

`base.html` provides the page shell (nav links to the 5 views, minimal
CSS — no framework, no CDN dependency, self-contained inline `<style>`).
Each view template extends it. Money/percentage formatting is a small
shared Jinja filter (e.g. `{{ value|money }}` → `$100,000.00`), not
duplicated per template.

## 5. Testing

`fastapi.testclient.TestClient` against the app, using the existing
`db_session` fixture pattern from `tests/conftest.py` (the app's session
factory is swapped for the test's `db_session` via FastAPI's dependency
override mechanism — a `get_db` dependency function in `app.py` that
production code uses via `Depends(get_db)`, and tests override with a
fixture that yields the transactional `db_session`). Seed a few rows per
test, hit the route, assert `200` and that expected values appear in the
rendered HTML body. One test per route for the happy path, plus: `/`
with no data yet (empty state, e.g. fresh DB — must not 500), `/decisions/
{id}` with a nonexistent id (404), `/decisions?ticker=` filtering
actually narrows results.

## 6. What this does NOT cover

Any write action (circuit breaker clearing stays a separate future
increment). Authentication. Charts/graphs beyond plain tables. Pagination
beyond newest-first ordering. Auto-refresh/live-updating (a manual page
reload is enough for a single-operator, on-demand tool). Production
hosting/exposure beyond localhost — this is explicitly a local-only tool
for the same reason the scheduler is dev/test-only right now.
