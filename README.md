# Bot-Trading

An autonomous **paper-trading** research system. Twice a day it runs a
multi-agent LLM research pipeline ([TradingAgents](https://github.com/TauricResearch/TradingAgents))
on a dynamically selected set of tickers, hands each decision to a deterministic (non-LLM)
risk-validation layer, and — if the decision survives validation — places a
limit order through Alpaca's paper-trading API. Every run, decision, order,
fill, and circuit-breaker event is journaled to a Postgres "second brain,"
viewable — and, for the scheduler/watchdog processes, controllable —
through a small dashboard.

Full design rationale, confirmed risk numbers, and open items live in
[`ARCHITECTURE.md`](ARCHITECTURE.md) — this file is about running it.

**Paper trading only.** Nothing here places live orders. Moving to live
capital requires an explicit, separate, written decision (§8 of
`ARCHITECTURE.md`).

---

## How it works

```
TradingAgents  →  Risk Validation Layer  →  Alpaca (paper)
(Ollama-backed)    (deterministic, non-LLM)
       │                    │                     │
       └────────────────────┴─────────────────────┘
                             ▼
              Second Brain (Postgres + pgvector)
```

Each scheduled cycle, per ticker (`orchestration/cycle.py`):

1. **Housekeeping** — record a heartbeat, sync fills for open orders,
   ingest any newly-resolved TradingAgents memory-log entries, ensure
   today's portfolio snapshot exists.
2. **Market status check** — stop here if the market's closed.
3. **Stop-loss check** — force-exit any position down more than 8% from its
   average entry price, regardless of circuit-breaker state.
4. **Circuit-breaker gate** — stop here if a daily/weekly drawdown breaker
   is tripped (cleared only by a human, via the CLI below).
5. **Research** — TradingAgents' full pipeline (4 analysts → bull/bear
   debate → trader → risk team → portfolio manager) via Ollama, producing a
   5-tier rating collapsed to BUY/SELL/HOLD plus a full debate transcript.
6. **Trade** — the decision is handed to the risk validation layer, the
   *only* code path allowed to call Alpaca's order endpoint. It
   independently re-derives position size, cash reserve, and exposure —
   it never trusts the agent's own risk math.
7. **Journal** — every run (including no-action and market-closed days) is
   written to the second brain, and a Discord alert fires on trades,
   rejections, and breaker trips.

Two independent processes run at all times: the **scheduler** (drives the
cycle above) and a **watchdog** (a second process that alerts if the
scheduler itself dies or hangs — nothing inside a dead process can alert
on its own failure).

---

## Repository layout

```
src/tradingsystem/
  config.py                  Settings (.env) + risk_config.yaml / candidate_universe.yaml loaders
  decision_engine/
    runner.py                Runs TradingAgentsGraph.propagate(), persists decisions + debate transcripts
    ta_config.py              Builds TradingAgents' config dict from our Settings
  risk/
    validation.py             Deterministic order validation (the only path that may submit to Alpaca)
    position_sizing.py        Converts a rating into a buy/sell OrderProposal
    stop_loss.py               Pure stop-loss trigger check
    circuit_breaker.py         Pure daily/weekly drawdown trip check
    kill_switch.py             External-file kill switch check
  execution/
    alpaca_client.py           The only module that talks to Alpaca's API
    executor.py                 Ties validation + kill switch + submission + persistence together
    realized_pnl.py             Average-cost-basis P&L on each sell fill
  orchestration/
    cycle.py                    Composes everything above into one scheduled cycle
    scheduler.py                 APScheduler entry point — the live process
    watchdog.py                  Second process: alerts if the scheduler dies/hangs
    heartbeat.py, discord_alerts.py, memory_ingestion.py
  db/
    models.py, repositories.py, session.py, init_db.py, clear_breaker.py
  dashboard/                    FastAPI + Jinja2 web UI (read-only views + process control)
config/
  risk_config.yaml              Risk numbers (position size, stop-loss, breakers) — edit freely
  candidate_universe.yaml        Discovery-screen candidate pool — edit freely, takes effect next cycle
tests/                          pytest, runs against a dedicated trading_test database
docker-compose.yml               Postgres + pgvector
```

---

## First-time setup

1. **Docker Desktop**, for Postgres:
   ```powershell
   docker compose up -d
   ```
   Brings up `trading_postgres` (pgvector/pgvector:pg16) on port 5432.

2. **Python 3.12 venv** (3.14 is avoided — too new for the
   langchain-heavy TradingAgents dependency chain to be trusted yet):
   ```powershell
   python3.12 -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -e ".[dev]"
   ```
   (git-bash: `source .venv/Scripts/activate` instead of the `Activate.ps1` line.)

3. **Ollama**, for the decision engine (a local `ollama serve` is required
   even when using cloud models — see Configuration below):
   ```powershell
   ollama pull qwen2.5:7b-instruct
   ```
   Only needed if you intend to run local models; if you're going straight
   to Ollama Cloud models, `ollama serve` running and signed in is enough
   (a `ollama pull <name>:cloud` once creates the local reference).

4. **`.env`**: copy `.env.example` to `.env` and fill in your Alpaca paper
   keys at minimum. See Configuration below for every field.

5. **Database schema**:
   ```powershell
   .venv\Scripts\python.exe -m tradingsystem.db.init_db
   ```
   Creates the `pgvector` extension and all tables in `trading`. Repeat
   against `trading_test` if it doesn't already exist (the docker-compose
   setup creates it automatically via `docker/init-test-db.sql`).

6. **Verify**:
   ```powershell
   .venv\Scripts\python.exe -m pytest
   ```
   Should be fully green before you run anything live.

Every command below assumes the venv is active
(`.venv\Scripts\Activate.ps1`) or you're invoking
`.venv\Scripts\python.exe` directly.

---

## Running it

### The live scheduler (the actual trading loop)

```powershell
python -m tradingsystem.orchestration.scheduler
```

Runs unattended, twice daily on the cron schedule in `.env`
(`PRE_MARKET_CRON` / `MIDDAY_CRON`, default ~9:35 and 12:30 ET,
weekdays). This is a long-running foreground process — start it in its
own terminal window and leave it running. There's no process-level
auto-restart on crash or reboot; if you need that, wrap it in your own
supervisor.

### The heartbeat watchdog (run alongside the scheduler, always)

```powershell
python -m tradingsystem.orchestration.watchdog
```

A **second**, independent long-running process. Checks periodically
(`WATCHDOG_CHECK_INTERVAL_MINUTES`, default 20) whether a cycle fired when
the cron schedule said it should have, and sends a Discord alert if the
scheduler appears to have died or hung. Start this in its own terminal
window too — it does nothing useful bundled into the scheduler's process,
since the whole point is surviving that process's death.

### The dashboard (on demand)

```powershell
python -m tradingsystem.dashboard
```

Serves `http://127.0.0.1:8787` by default (`DASHBOARD_HOST` /
`DASHBOARD_PORT` in `.env`). Six views: overview (latest snapshot,
heartbeat, active breakers), decisions/journal, decision detail (full
debate transcript), orders/fills, P&L, and control (start/stop the
scheduler and watchdog, trigger an off-cycle run). Process control is the
dashboard's only write capability — starting/stopping those two processes
and kicking off an off-cycle cycle. Every write route is protected by a
same-origin check (CSRF guard) — not full authentication, appropriate for
this single-operator, localhost-only tool, but note it still means an
unauthenticated local client can start/stop the live trading process, not
just view data. Start the dashboard whenever you want to look or manage
the processes, stop it whenever you're done; the scheduler and watchdog
keep running independently either way.

Stopping honors an in-flight research cycle (finishes the current ticker,
declines the next); a stop that doesn't complete within 60s reports
"still running" rather than auto-killing — an explicit separate Force Stop
action is required to kill immediately, because a forced kill mid-order
can orphan a live order at the broker with no local record.
`run/scheduler.log`, `run/watchdog.log`, and `run/run_once.log` are the
three log files under the gitignored `run/` directory, viewable on the
`/control` page.

### Clearing a tripped circuit breaker

Circuit breakers (daily 3% / weekly 8% drawdown) are **manual-reset only**
— nothing in this system can clear one automatically:

```powershell
python -m tradingsystem.db.clear_breaker daily --cleared-by "Harry" --note "reviewed today's drawdown, resuming"
python -m tradingsystem.db.clear_breaker weekly --cleared-by "Harry" --note "..."
```

Both flags are required — a real name and a real review note are always
on record for every clear. Check the dashboard's overview page first to
see what's currently active.

### The kill switch

An emergency stop, independent of the scheduler process (so a stuck
process can't bypass it — the validation layer checks this before *every*
order):

```powershell
# Halt: create the file (any content, even empty)
New-Item -ItemType File -Path $env:KILL_SWITCH_FILE -Force

# Resume: delete it
Remove-Item $env:KILL_SWITCH_FILE
```

The path is `KILL_SWITCH_FILE` in `.env` (defaults to `KILL_SWITCH` at the
repo root). The scheduler keeps running either way — this only blocks new
order submissions.

### Tests

```powershell
python -m pytest            # full suite
python -m pytest tests/test_stop_loss.py -v   # one file
```

Tests run against a **dedicated `trading_test` database**
(`TEST_DATABASE_URL`), never the live `trading` database the scheduler
writes to — safe to run anytime, including while the scheduler is live.

---

## Configuration

### `.env` (copy from `.env.example`, gitignored)

| Variable | Default | Notes |
|---|---|---|
| `TRADING_MODE` | `paper` | Must stay `paper` until an explicit written go-live decision. `AlpacaClient` refuses to construct otherwise. |
| `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_API_SECRET` | — | Required. From your Alpaca paper account. |
| `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_API_SECRET` | — | Kept separate from paper keys so a config mistake can't silently trade live. Unused until go-live. |
| `DATABASE_URL` | `.../trading` | The live/dev database. |
| `TEST_DATABASE_URL` | `.../trading_test` | Dedicated to the test suite — never touched by the scheduler. |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Always points at your local `ollama serve`, even for `:cloud` models (the local daemon proxies those to Ollama Cloud using your signed-in account — no separate API key). |
| `TRADINGAGENTS_DEEP_THINK_MODEL` | `qwen2.5:7b-instruct` | Research Manager, Trader, Risk Judge, Portfolio Manager. Set to `gpt-oss:120b-cloud` or similar for Ollama Cloud. |
| `TRADINGAGENTS_QUICK_THINK_MODEL` | `qwen2.5:7b-instruct` | The four analysts. e.g. `nemotron-3-super:cloud`. |
| `TRADINGAGENTS_MAX_DEBATE_ROUNDS` | `1` | Bull/bear debate depth. |
| `TRADINGAGENTS_MAX_RISK_DISCUSS_ROUNDS` | `1` | Risk-team debate depth. |
| `TRADINGAGENTS_RUN_MAX_ATTEMPTS` | `2` | Outer retry budget if the whole research call fails. |
| `TRADINGAGENTS_MEMORY_LOG_PATH` | *(empty = TradingAgents' own default)* | Only set to relocate `trading_memory.md`. |
| `PRE_MARKET_CRON` / `MIDDAY_CRON` | `35 9 * * mon-fri` / `30 12 * * mon-fri` | Always interpreted in America/New_York regardless of host locale. |
| `DISCOVERY_SLOTS_PER_CYCLE` | `4` | Number of discovery slots filled from the candidate universe each cycle, on top of always-reassessed held positions — tune based on observed API usage. |
| `DISCORD_WEBHOOK_URL` | — | Optional but recommended — alerts are just logged if unset. |
| `HEARTBEAT_GRACE_MINUTES` | `30` | How overdue a cycle must be before the watchdog alerts. |
| `WATCHDOG_CHECK_INTERVAL_MINUTES` | `20` | How often the watchdog checks. |
| `KILL_SWITCH_FILE` | `<repo>/KILL_SWITCH` | See Kill switch above. |
| `DASHBOARD_HOST` / `DASHBOARD_PORT` | `127.0.0.1` / `8787` | |

**Not in `.env` at all** — TradingAgents' optional data vendors read these
directly from the process environment, bypassing our own `Settings`
entirely. `scheduler.py` loads `.env` into the real process environment on
startup specifically so these work if you set them:

| Variable | What it enables |
|---|---|
| `FRED_API_KEY` | Macro data (rates, CPI, unemployment, etc.) — free key at https://fred.stlouisfed.org/docs/api/api_key.html |
| `ALPHA_VANTAGE_API_KEY` | Additional market data vendor — free tier available |

Both are optional; TradingAgents degrades gracefully (just skips that
data source) if unset.

### `config/risk_config.yaml`

The risk numbers confirmed in `ARCHITECTURE.md` §4 — max position size,
cash reserve, stop-loss %, breaker thresholds, stale-data window. Edit
freely; changes apply on the next cycle, no code change or restart
required for most fields (the running scheduler re-reads this file each
cycle). Treated as gospel by the validation layer — it never trusts the
agent's own risk math.

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

---

## Typical operating routine

- Keep the **scheduler** and **watchdog** running at all times, each in
  its own terminal window.
- Open the **dashboard** whenever you want to check in — decisions,
  orders, P&L, active breakers.
- If Discord alerts you to a tripped breaker, review, then clear it via
  the CLI above.
- If something looks wrong and you need to stop trading immediately
  without killing the scheduler process, use the **kill switch**.
