# Orchestration Loop — Design Spec

Status: **Approved by user 2026-08-24. Ready for implementation planning.**

This spec covers the one piece ARCHITECTURE.md explicitly left unbuilt: the
process that actually composes the existing, individually-tested modules
(`decision_engine/runner.py`, `risk/validation.py`, `risk/circuit_breaker.py`,
`risk/kill_switch.py`, `risk/position_sizing.py`, `execution/executor.py`)
into the twice-daily, per-ticker research → validate → execute → journal loop
described in ARCHITECTURE.md §2 and §6.

Context that motivated a few decisions below: a live smoke test on 2026-08-24
(GPU desktop, RTX 4060 Ti, 8GB VRAM) showed `run_research()` for one ticker
takes ~22 minutes end-to-end against `qwen2.5:7b-instruct` via Ollama. With
only one model fitting in 8GB VRAM, tickers must be researched strictly
sequentially — a 3-ticker cycle costs roughly an hour of wall time.

---

## 1. Scope

In scope: the standalone orchestration process, its per-cycle pipeline, a
minimal Discord alert sender, and a heartbeat mechanism.

Out of scope (separate, later milestones, not touched here): the dashboard,
production service packaging (systemd/Windows service — this desktop is a
dev/test environment for now, not the confirmed 24/7 host), backtesting,
and going live with real capital.

---

## 2. Module layout

New package `src/tradingsystem/orchestration/`:

- **`cycle.py`** — `run_full_cycle(session, run_type, settings=None)`. The
  actual composed pipeline. Framework-free (no APScheduler import) so it's
  directly unit-testable.
- **`scheduler.py`** — APScheduler wiring only. Entry point:
  `python -m tradingsystem.orchestration.scheduler`. Two cron jobs
  (`pre_market`, `midday`) each calling `run_full_cycle` with a fresh
  session per invocation.
- **`heartbeat.py`** — `record_heartbeat(session, run_type, ticker=None)`
  and `get_heartbeat(session)`, backed by the new `SchedulerHeartbeat` table.
- **`discord_alerts.py`** — `send_alert(settings, message, level="info")`.
  A single `requests.post` (or `httpx`, matching whatever's already a
  transitive dependency) to `settings.discord_webhook_url`. No-op (log only)
  if the URL is empty, so dev/test runs without a configured webhook don't
  crash.

## 3. Upstream change required

`ResearchResult` in `decision_engine/runner.py` currently omits the
persisted `Decision` row's own primary key. `executor.place_order()` needs
that id as its `decision_id` foreign key parameter. Add one field:

```python
@dataclasses.dataclass(frozen=True)
class ResearchResult:
    ok: bool
    agent_run_id: uuid.UUID
    decision_id: uuid.UUID | None = None   # NEW
    rating: str | None = None
    decision: str | None = None
    reasoning_summary: str | None = None
```

Populated from `decision.id` in `run_research`'s existing success path
(right where `Decision(...)` is already constructed and flushed). No other
change to that module's behavior.

## 4. New DB table: `SchedulerHeartbeat`

Single-row table (component-keyed, in case of future multi-component
monitoring, but only one row — `component="scheduler"` — is written by this
milestone):

```python
class SchedulerHeartbeat(Base):
    __tablename__ = "scheduler_heartbeats"

    component: Mapped[str] = mapped_column(primary_key=True)  # "scheduler"
    last_seen_at: Mapped[datetime.datetime]
    last_run_type: Mapped[str | None]     # "pre_market" | "midday"
    last_ticker: Mapped[str | None]       # ticker most recently processed, or None between cycles
```

`record_heartbeat` does an upsert (fetch-or-create the `component="scheduler"`
row, update in place). A future dashboard/monitor can alert on
`now() - last_seen_at` exceeding some threshold; that monitor is out of
scope here.

## 5. New `Settings` fields

```python
pre_market_cron: str = "0 8 * * mon-fri"    # America/New_York, APScheduler cron syntax
midday_cron: str = "30 12 * * mon-fri"      # America/New_York
```

Both overridable via `.env`, consistent with the existing config-not-code
philosophy. The APScheduler `CronTrigger`s are constructed with
`timezone="America/New_York"` explicitly, regardless of host machine locale
— the scheduling intent is tied to US market hours, not wherever the process
happens to run.

## 6. Per-cycle pipeline (`run_full_cycle`)

Called once per scheduled firing, with `run_type` fixed to `"pre_market"` or
`"midday"` by the caller (`scheduler.py`).

```
1. heartbeat: record_heartbeat(session, run_type)  # cycle started
2. market_status = alpaca_client.get_clock()
3. if market_status != "open":
     for ticker in watchlist:
         AgentRun(ticker, run_type, market_status, outcome="market_closed",
                   started_at=now, finished_at=now)
     session.commit()
     return                                          # ARCHITECTURE.md §2: market check first, stop if closed

4. ensure_snapshot_baseline(session, alpaca_client)   # see §7 below

5. breaker = check_breakers(session, alpaca_client, risk_config)
   if breaker.blocked:
       for ticker in watchlist:
           AgentRun(ticker, run_type, market_status,
                     outcome="circuit_breaker_active", started_at=now, finished_at=now)
       session.commit()
       return

6. for ticker in watchlist:
       try:
           result = run_research(session, ticker, trade_date=today_iso(), 
                                   market_status=market_status, settings=settings)
           heartbeat.record_heartbeat(session, run_type, ticker=ticker)

           if not result.ok or result.decision == "hold":
               session.commit()
               continue

           portfolio = executor.build_portfolio_state(alpaca_client)
           price = alpaca_client.get_latest_price(ticker)
           proposal = size_order(result.rating, ticker, portfolio, price,
                                   risk_config.max_position_pct, data_timestamp=now())

           if proposal is not None:
               exec_result = executor.place_order(
                   session, alpaca_client, proposal, result.decision_id,
                   settings.kill_switch_file, risk_config.max_position_pct,
                   risk_config.cash_reserve_pct, risk_config.stale_data_max_age_minutes,
               )
               if exec_result.submitted:
                   discord_alerts.send_alert(settings, f"Order placed: {ticker} ...", level="info")
               else:
                   discord_alerts.send_alert(settings, f"Order rejected/failed: {ticker} — {exec_result.rejection_reasons or exec_result.error}", level="warning")

           session.commit()
       except Exception as exc:
           log.critical("unhandled error processing %s: %s", ticker, exc)
           discord_alerts.send_alert(settings, f"Cycle error on {ticker}: {exc}", level="critical")
           session.rollback()
           continue

7. heartbeat.record_heartbeat(session, run_type)      # cycle finished
   session.commit()
```

Notes:
- Price is fetched live, immediately before `size_order`/`place_order` — so
  `OrderProposal.data_timestamp` is always effectively "now" and the
  stale-data check in `validate_order` is a genuine safety net for slow
  paths, not a routine trip.
- `place_order` already re-checks the kill switch and re-derives portfolio
  state independently before submitting — no duplicate check needed here.
- One ticker's uncaught exception does not stop the rest of the watchlist;
  it's logged, alerted, rolled back, and the loop continues.

## 7. Circuit breaker baseline mechanics

```
ensure_snapshot_baseline(session, alpaca_client):
    today = date.today()  # America/New_York
    if no PortfolioSnapshot exists for today:
        account = alpaca_client.get_account()
        session.add(PortfolioSnapshot(snapshot_date=today, equity=account.equity,
                                        cash=account.cash, positions=alpaca_client.get_positions()))
        session.flush()

check_breakers(session, alpaca_client, risk_config):
    day_start = PortfolioSnapshot for today
    week_start = earliest PortfolioSnapshot with snapshot_date >= this_monday
    current_equity = alpaca_client.get_account().equity

    daily = check_daily_breaker(day_start.equity, current_equity, risk_config.daily_drawdown_breaker_pct)
    weekly = check_weekly_breaker(week_start.equity, current_equity, risk_config.weekly_drawdown_breaker_pct)

    for result in (daily, weekly):
        if result.tripped and no active CircuitBreakerEvent of that type:
            record_breaker_trip(session, result.breaker_type, reason=...)
            discord_alerts.send_alert(settings, f"CIRCUIT BREAKER TRIPPED: {result.breaker_type} drawdown {result.drawdown_pct:.2%}", level="critical")

    blocked = daily.tripped or weekly.tripped or get_active_breaker_event(session, "daily") or get_active_breaker_event(session, "weekly")
    return BreakerGateResult(blocked=blocked, ...)
```

Because the first cycle of a new day/week snapshots *before* checking, that
first cycle always compares current equity to itself (drawdown 0) — it can
never spuriously trip on the transition into a new day/week.

## 8. Discord alert triggers (ARCHITECTURE.md §7, confirmed)

- Circuit breaker trip (critical)
- Order rejected or execution error (warning)
- Every order successfully placed (info — full visibility, not just failures)
- Unhandled per-ticker exception (critical)

Heartbeat *failure* (staleness) is inherently detected externally (by
whatever eventually reads `SchedulerHeartbeat`, e.g. a future dashboard) —
this milestone only writes the heartbeat, it doesn't alert on its own
absence, since a crashed process can't reliably alert about itself.

## 9. Testing

Same pattern as the existing suite:
- `tests/conftest.py`'s `db_session` fixture (real Postgres, transactional
  rollback) — no new test infrastructure needed.
- `run_research` mocked the same way `test_decision_engine_runner.py`
  already does (a scripted `TradingAgentsGraph` stand-in), so cycle tests
  never call real Ollama.
- `AlpacaClientProtocol` mocked with a scripted fake, same shape as
  whatever `tests/test_executor.py` already uses.
- Cases to cover: market closed → no research called, all tickers journaled;
  circuit breaker active → same; breaker newly trips mid-cycle → alert sent,
  event recorded, remaining tickers skipped; one ticker raises → others still
  processed; hold decision → no order attempt; buy/sell decision → proposal
  sized and submitted.

## 10. What this does NOT cover

Dashboard, production service packaging for whichever machine ends up being
the real 24/7 host, Discord alert *formatting* polish (message content above
is illustrative, not final copy), and any backtesting or go-live-readiness
tracking. Those are separate future milestones per ARCHITECTURE.md §§7–8.
