# Autonomous Trading System — Architecture

Status: **CONFIRMED — signed off by user. Implementation in progress.**

This document summarizes the design agreed in the interview covering the
`trading-system-claude-code-brief.md`. Nothing in this repo should be built
until you've reviewed and confirmed this.

---

## 1. System overview

Three independently-testable components, connected by a deterministic
validation boundary between judgment and execution:

```
┌─────────────────┐     decision      ┌──────────────────────┐     validated order   ┌─────────────┐
│   TradingAgents   │ ───(BUY/SELL/  ─▶│   Risk Validation     │ ─────(if passes)─────▶│   Alpaca    │
│  (Ollama-backed)  │     HOLD +       │   Layer (deterministic,│                       │  (paper API)│
│                    │     reasoning)   │   non-LLM)             │                       │             │
└─────────────────┘                   └──────────────────────┘                       └─────────────┘
         │                                       │                                            │
         │  full debate transcript               │  pass/fail + reason                        │  orders/fills
         ▼                                       ▼                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                          Second Brain (Postgres + pgvector)                                        │
│   agent_runs · decisions · debate_transcripts · orders · fills · portfolio_snapshots · circuit_    │
│   breaker_events · trading_memory_entries                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Orchestration is a **standalone Python scheduler process** (APScheduler,
in-process — see §6 for how it actually runs) that calls Ollama directly.
**Claude Code is a dev-tool only** — it builds and maintains this codebase
but is never in the runtime path and never bills per trading run. This was
explicitly confirmed rather than assumed, per the brief's flag.

---

## 2. Data flow (per scheduled run)

Run twice daily (pre-market and midday). Actual step order, per ticker
cycle (as implemented, `orchestration/cycle.py`):

1. **Pre-flight housekeeping** — every run, independent of market status:
   record a heartbeat, sync fill status for any still-open orders, ingest
   any newly-resolved TradingAgents memory-log entries, and ensure today's
   portfolio snapshot baseline exists.
2. **Market status check** — if market closed/unavailable, log a journal
   entry and stop here.
3. **Stop-loss check** — deterministic, runs before the circuit-breaker
   gate below: any live position down more than the stop-loss threshold
   (§4) from its average entry price is force-exited immediately,
   regardless of circuit-breaker state — a stop-loss is a defensive exit,
   not new discretionary risk.
4. **Circuit-breaker gate** — if a daily/weekly breaker is active, log a
   journal entry and stop here.
5. **Research stage** — TradingAgents runs its full pipeline (fundamentals,
   sentiment, news, technical analysts → bull/bear researchers → trader →
   risk team → portfolio manager) via Ollama, 2 debate rounds (§5). Produces a
   decision (5-tier rating, collapsed to BUY/SELL/HOLD) + reasoning + full
   debate transcript. Every structured output is schema-validated; malformed
   output triggers a retry, and persistent failure fails closed (no trade).
6. **Trade stage** — the decision is handed to the deterministic risk
   validation layer (see §4). This layer — not TradingAgents — is the only
   code path allowed to call Alpaca's order endpoint. It independently
   re-derives position size, cash reserve, and exposure; it does not trust
   any risk math the agent claims to have already done.
7. **Journal stage** — every run, including no-action/HOLD days and days
   where market was closed, writes a journal entry to the second brain.

A heartbeat is emitted independently of trade activity, so a silent crash
is distinguishable from a legitimate no-trade day.

---

## 3. Second-brain schema (Postgres + pgvector)

Single Postgres instance, `pgvector` extension for embeddings (avoids
running a separate vector DB). Core tables:

- **`agent_runs`** — one row per scheduled research run: ticker, run type
  (pre-market/midday), timestamp, market-status result, outcome.
- **`decisions`** — final BUY/SELL/HOLD + reasoning summary per run, FK to
  `agent_runs`.
- **`debate_transcripts`** — full analyst reports + bull/bear debate turns +
  risk-team discussion per run, each entry embedded for semantic search.
- **`orders`** / **`fills`** — every order submitted (post-validation),
  status, fill price/qty/timestamp, linked back to the originating decision.
- **`portfolio_snapshots`** — daily equity/cash/positions snapshot.
- **`realized_pnl`** — per closed trade, linked to originating decision(s).
- **`circuit_breaker_events`** — every trip, trigger reason, timestamp,
  manual-clear timestamp + note (who/when it was reviewed and resumed).
- **`trading_memory_entries`** — TradingAgents' own `trading_memory.md`
  reflection log, ingested/parsed into the same store rather than left as a
  separate disconnected file.

This supports both "why did it buy X on date Y" (join `decisions` →
`debate_transcripts`) and "how has it performed since" (join `orders` →
`fills` → `realized_pnl` → `portfolio_snapshots`).

---

## 4. Risk controls (confirmed numbers)

| Control | Value |
|---|---|
| Mode | **Paper trading only**, until explicit written sign-off after evaluation period |
| Ticker selection | Dynamic per cycle: held positions always reassessed (uncapped) + a configurable number of discovery slots screened from a larger candidate pool (equities/ETFs only, long-only, no options/shorting) — freely editable config, not hardcoded; see note below |
| Paper balance | $100,000 (Alpaca default) |
| Max position size | 10% of portfolio per position |
| Cash reserve | 20% minimum always unallocated |
| Stop-loss | 8% fixed, per position |
| Daily drawdown circuit breaker | 3% |
| Weekly drawdown circuit breaker | 8% |
| Circuit breaker reset | **Manual only, by you** — full review required, no automated or agent-triggered clear |
| Human approval gate (paper mode) | None — fully unattended in paper from day one; deterministic validation + circuit breakers are the only gates |
| Order type | Limit orders only, enforced in code |
| Order validation checks | Position size / cash reserve / exposure caps, market status, stale-data check, duplicate-order check — all deterministic, independent of agent's own claims |
| Error handling | Log + alert on partial fills, rejections, API errors — **no automatic retry/resubmission** |

---

**Ticker selection is dynamic, not a hardcoded watchlist.** Every cycle,
any ticker with a currently open position is always reassessed (uncapped),
plus a configurable number of "discovery" slots (`DISCOVERY_SLOTS_PER_CYCLE`)
filled from a larger candidate pool via a deterministic momentum +
relative-volume screen — no LLM cost for the screen itself, only for the
tickers it actually selects. The candidate pool is
`config/candidate_universe.yaml`, freely editable, not hardcoded; see
`docs/superpowers/specs/2026-08-25-ticker-selection-design.md` for the full
design. You're responsible for your own judgment calls on a new candidate's
liquidity/coverage before adding it; the system doesn't gate this
automatically. Worth remembering: each ticker actually selected for a cycle
adds real local-inference time (more Ollama calls per cycle), so watch total
cycle time as held positions and `DISCOVERY_SLOTS_PER_CYCLE` grow.

---

## 5. Decision engine configuration

- Models: **deep-think** (`gpt-oss:120b-cloud`) and **quick-think**
  (`nemotron-3-super:cloud`) via Ollama Cloud — deep-think for the Research
  Manager, Trader, Risk Judge, and Portfolio Manager; quick-think for the
  four analysts. Both `.env`-configurable
  (`TRADINGAGENTS_DEEP_THINK_MODEL` / `TRADINGAGENTS_QUICK_THINK_MODEL`),
  independently swappable with no code change. Ollama Cloud is proxied
  through the same local `ollama serve` daemon using the signed-in
  account — no separate API key, but `ollama serve` must still be running
  locally. Originally a single local Qwen2.5:7b-instruct model (GPU-resident,
  one role-agnostic model to keep tool-calling validation simple); migrated
  after the local smoke test (§9) showed ~22 min/ticker was impractical for
  the twice-daily/3-ticker cadence unattended — see
  `docs/superpowers/specs/2026-08-25-ollama-cloud-model-split-design.md`
  for the full evaluation.
- `max_debate_rounds`: 2 (raised from 1 on 2026-09-03 as a tracked
  experiment — see below; `max_risk_discuss_rounds` stays at 1).
- Analysts: all four (fundamentals, sentiment, news, technical), equally
  weighted — TradingAgents' default full pipeline.
- Run cadence: **twice daily per ticker**, pre-market and midday.
- Every structured output from the decision engine is treated as untrusted:
  schema validated, retried on malformed output, fails closed (no trade) on
  persistent ambiguity — per the brief's non-negotiable on tool-calling
  reliability, regardless of which model is configured.

### Debate-rounds experiment (2026-09-03)

`tradingagents_max_debate_rounds` raised 1 -> 2. This is a tracked
experiment, not an assumed improvement — review after ~2 weeks of live
cycles.

**Review due ~2026-10-06, not ~09-17.** The droplet's database (§6) starts
fresh on 2026-09-22 — the Windows-era history was not migrated — so the
2-week window is counted from 09-22. There is also no pre-change (1-round)
baseline in that database; the comparison is round 1 vs. round 2 *within*
each run. Verified 2026-09-25: every run since 09-22 shows 2 bull and 2
bear turns.

1. Confirm two rounds actually ran. **Don't count `debate_transcripts`
   rows** — `runner.py`'s `_persist_debate_transcript` stores one row per
   role per run, holding TradingAgents' accumulated `bull_history` /
   `bear_history` (every round, each turn prefixed `Bull Analyst:` /
   `Bear Analyst:`), so row counts read 1/1 regardless of the setting.
   Count turns inside the content instead:
   ```sql
   SELECT a.created_at::date AS day, count(*) AS runs,
          round(avg(regexp_count(t.content, '(^|\n)Bull Analyst:')), 2) AS bull_turns,
          round(avg(regexp_count(b.content, '(^|\n)Bear Analyst:')), 2) AS bear_turns
   FROM agent_runs a
   JOIN debate_transcripts t ON t.agent_run_id = a.id AND t.role = 'bull_researcher'
   JOIN debate_transcripts b ON b.agent_run_id = a.id AND b.role = 'bear_researcher'
   GROUP BY 1 ORDER BY 1;
   ```
   Split a single run's turns apart with
   `unnest(regexp_split_to_array(content, '\nBull Analyst:')) WITH ORDINALITY`.
2. Read a sample of the second-round turns. Do they cite evidence not
   already present in the first round (a new report section, a number, a
   counter-argument), or do they mostly restate the first round in
   different words?
3. Cross-reference against `decisions.rating` for the same `agent_run_id`:
   did any second-round argument actually flip the eventual rating versus
   what round 1 alone was trending toward (visible in
   `debate_transcripts.role = 'research_manager_judge'` content)?
4. If the second round is consistently restating round 1 with no rating
   impact after a reasonable sample, revert `tradingagents_max_debate_rounds`
   to `1` in `src/tradingsystem/config.py` and note the outcome here rather
   than leaving the extra LLM cost with no evidence behind it.

### Tool-usage audit finding (2026-09-03)

Reading the pinned `tradingagents==0.3.1` source directly (not runtime
behavior — a static wiring check) found:

- `get_insider_transactions` **is** registered in the `"news"` `ToolNode`
  (`tradingagents/graph/trading_graph.py:214`, alongside `get_news`,
  `get_global_news`, `get_macro_indicators`, `get_prediction_markets`).
- `get_insider_transactions` is **not** in `news_analyst.py`'s own `tools`
  list (`tradingagents/agents/analysts/news_analyst.py:20-25`), which is
  what actually gets passed to `llm.bind_tools(tools)`. The News Analyst's
  LLM is therefore never offered `get_insider_transactions` as a callable
  tool at all — it is not a reliability question (the model isn't failing
  to call it; it structurally cannot), it's a gap in the pinned package.
- `get_macro_indicators` and `get_prediction_markets` *are* both in
  `news_analyst.py`'s `tools` list, so those two are reachable — whether the
  model actually chooses to call them on a given run is what
  `decision_engine/tool_audit.py`'s `ToolCallAuditCallback` (added
  2026-09-03) now logs per `AgentRun`. Review those logs after a few weeks
  of live cycles before concluding either way.

**Fix for the `get_insider_transactions` gap is deliberately not implemented
yet.** The only two paths are (a) patch
`tradingagents.graph.setup.create_news_analyst` at import time in our own
code — fragile against upstream changes, and a real behavior change to a
pinned dependency happening outside its own version pin, or (b) fork/vendor
the package. Given ARCHITECTURE.md's existing stance that risk-relevant
config changes are deliberate decisions, not routine tuning, this is left as
an open item for explicit sign-off rather than folded into this audit.

### Trend cross-check experiment (2026-09-22)

A dual moving-average crossover (`trend_check_short_ma_days` /
`trend_check_long_ma_days`, default 9/50) now shadow-logs onto non-Hold
`Decision` rows with enough bar history (at least `trend_check_long_ma_days`
days): `trend_signal` ("bullish"/"bearish"/"neutral") and `trend_agrees`
(whether the crossover direction matches the LLM rating's implied
direction) -- `trend_agrees` is also `NULL` when `trend_signal` is
`"neutral"`, since there's no directional call to agree or disagree with.
This does not affect sizing or order submission -- see
`docs/superpowers/specs/2026-09-22-trend-cross-check-design.md` for the
full design and rationale (built in response to the Alpha Audit's
closing recommendation, published artifact
`https://claude.ai/artifact/XpEv23wjPHuT4inhTkALFh`).

Review after a few weeks of live cycles:

1. Query `decisions` rows with `trend_agrees IS NOT NULL`, joined to
   `realized_pnl` for closed round-trips (same `decision_ids` join
   `get_realized_returns_by_rating` in `db/repositories.py` already uses).
   Note: `Decision` rows minted by the stop-loss path
   (`check_and_execute_stop_losses`) never have a trend check run and
   will always show `trend_signal IS NULL` -- this is expected, not
   missing data, and this join should attribute a closed round-trip's
   outcome to its entry-side `Decision` (the one with a real rating and
   possibly a trend flag), not its stop-loss exit row.
2. Compare realized % return (`pnl_amount / entry_notional`) where
   `trend_agrees = True` vs. `trend_agrees = False`. Require at least 10
   closed round-trips in each of the True/False buckets before drawing
   any conclusion -- the same minimum-sample-size bar `kelly_min_sample_size`
   already uses elsewhere in this codebase for the same reason. Also
   compare both buckets against the `trend_signal = 'neutral'` bucket as
   an informal control group, not just True vs. False.
3. If `trend_agrees = True` trades show a real, consistent edge after
   clearing that sample-size bar, that's the evidence needed to consider
   a follow-up design that discounts position size on disagreement --
   not something this experiment does automatically.
4. If there's no consistent difference, or the sample never reaches the
   minimum size above, that's still a useful result: this particular
   cross-check isn't adding information (or isn't yet decidable) for
   this system's tickers/cadence. Nothing downstream depends on it, so
   there is nothing to revert either way.

---

## 6. Scheduling & runtime architecture

- Standalone Python process using **APScheduler** (in-process, not OS-level
  cron) — chosen so circuit-breaker state and heartbeat status can be kept
  in memory within one coherent process rather than reconstructed from
  external state on every cron invocation. Entry point: `python -m
  tradingsystem.orchestration.scheduler`.
- **Host: a DigitalOcean Basic droplet (Ubuntu 24.04), live since
  2026-09-22.** Supersedes the original Windows desktop (RTX 4060 Ti),
  where the scheduler ran as a manually-started detached process with no
  auto-restart. The repo lives at `/home/deploy/Bot-Trading`, run as the
  `deploy` user. Postgres runs in Docker (`docker-compose.yml`, container
  `trading_postgres`); its data starts fresh on 2026-09-22 — the
  Windows-era database was not migrated, so any review query over history
  only sees droplet-era rows.
- **Three long-running systemd services** (`deploy/systemd/`): `bot-scheduler`,
  `bot-watchdog`, `bot-dashboard`, each `Restart=on-failure` with a 10s
  back-off, so a crash or reboot no longer needs a manual restart. Note
  `on-failure` means a *clean* exit (exit 0 — e.g. after a stop request)
  is **not** restarted; that's intentional, and what `deploy/update.sh`
  relies on.
- A second, independent **heartbeat watchdog** process (`python -m
  tradingsystem.orchestration.watchdog`) runs alongside the scheduler,
  checking every ~20 minutes (configurable) whether a cycle fired when the
  cron schedule says it should have, and alerting via Discord if not —
  necessary because nothing inside a dead or hung scheduler process can
  alert on its own failure.
- Ollama still runs locally on the droplet (`ollama serve`, at
  `OLLAMA_BASE_URL`, default `localhost:11434`) as the proxy for Ollama
  Cloud (§5) — all inference happens on Ollama's infrastructure, but the
  local daemon must be running and signed in. It is not one of this repo's
  systemd units.
- **Memory: the droplet needs swap.** A 2GB swapfile (`vm.swappiness=10`)
  was added 2026-09-25 after the Vite production build was OOM-killed with
  Postgres + the three services resident. Without it, a memory spike can
  also OOM-kill the scheduler or Postgres, not just a build.
- Kill switch is an independent mechanism — not a flag the agent checks in
  its own loop — implemented as an external file check that the validation
  layer consults before every order, so a stuck/broken agent process can't
  bypass it.

### Deploying updates

Run `./deploy/update.sh` on the droplet as `deploy`. It:

1. Refuses to run on a dirty working tree (never discard changes blindly —
   inspect `git status` first; a rewritten `frontend/package-lock.json`
   from a manual `npm install` is the usual culprit and is safe to
   `git checkout --`).
2. `git pull --ff-only`, then diffs against the last **fully deployed**
   commit recorded in `run/deployed_commit` (not the pre-pull HEAD), so a
   failed step is retried on re-run rather than reported "already up to
   date". No marker → full deploy.
3. Reinstalls Python deps if `pyproject.toml` changed (`pip install -e .`,
   no dev extras) and rebuilds the frontend if `frontend/` changed
   (`npm ci && npm run build` — `npm ci` never rewrites the lockfile).
4. If `db/models.py` changed, shows the diff and blocks for confirmation:
   **there is no Alembic**; `init_db.py` only creates missing tables, so
   new/changed columns must be applied by hand with `ALTER TABLE` against
   the live `trading` database before continuing.
5. Stops the scheduler **cooperatively** via `run/scheduler.stop_requested`
   (it finishes the ticker in progress, skips the rest of the cycle, exits
   cleanly) rather than letting `systemctl restart`'s SIGTERM kill it
   mid-order. Prefer deploying outside the 09:35 / 12:30 ET cycles.
6. Restarts all three services, verifies each is active individually, and
   only then writes `run/deployed_commit`.

The droplet runs Node 18 (Ubuntu's apt default, EOL). The build works —
Vite 5.4 supports it — but Playwright's `EBADENGINE` warnings on `npm ci`
are expected until Node is upgraded to 20+.

### Dashboard process control under systemd

The dashboard's Control page (§7) detects systemd (`INVOCATION_ID`, which
systemd sets for every unit) and drives `systemctl` instead of spawning or
killing PIDs itself — on the Windows dev box it keeps the original
detached-spawn behavior:

- **Start** → `sudo -n systemctl start bot-<name>`, then polls the pidfile.
- **Stop** → unchanged: the cooperative stop-request file. The process
  finishes its current ticker and exits 0, which `Restart=on-failure`
  leaves stopped until Start is pressed.
- **Force Stop** → `sudo -n systemctl stop bot-<name>` (SIGTERM, then
  SIGKILL after systemd's stop timeout). A bare PID kill would just be
  restarted 10s later. Same orphaned-order caveat as always (§7).
- **Run now** → `sudo -n systemctl start --no-block bot-run-once`, a
  oneshot unit (`deploy/systemd/bot-run-once.service`, never enabled). It
  runs in its own cgroup, so restarting the dashboard can't kill it, and a
  second press while one is running joins that run rather than starting
  another.

`deploy` gets exactly those five commands passwordless via
`deploy/sudoers/bot-trading` — nothing broader. If sudo refuses (file not
installed, argv drift between the file and `process_control.py`), the
route returns a 500 carrying sudo's error rather than a false success.

One-time install on the droplet:
```bash
cd ~/Bot-Trading
sudo cp deploy/systemd/bot-run-once.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo visudo -cf deploy/sudoers/bot-trading && \
  sudo install -m 0440 -o root -g root deploy/sudoers/bot-trading /etc/sudoers.d/bot-trading
```

---

## 7. Monitoring & alerting

- **Discord webhook** for notifications.
- **Immediate alerts** on: circuit breaker trips, order rejections/API
  errors, scheduler heartbeat failure, and every trade placed (full
  visibility, not just failures).
- **Dashboard** over the second brain — rebuilt 2026-08-31/09-01 as a
  React/TypeScript SPA (Vite build, `frontend/`) served by FastAPI
  (`src/tradingsystem/dashboard/`) as static files (`frontend/dist/`,
  built via `cd frontend && npm run build`) with an SPA fallback route,
  plus a JSON API under `/api/*` that the old server-rendered Jinja2
  templates were fully retired in favor of. On the droplet it runs
  continuously as the `bot-dashboard` systemd service (§6), still bound to
  `127.0.0.1:8787` — never exposed publicly; reach it through an SSH tunnel
  (`ssh -L 8787:127.0.0.1:8787 deploy@<droplet>`, then
  `http://localhost:8787`). Nine views now, not seven: the original
  seven (overview, decisions/journal — filterable by ticker, includes
  no-action days; decision detail with the full ordered debate
  transcript; orders/fills with per-order cancel; P&L history; config —
  candidate universe, risk config, and select `.env` settings editing;
  and control — scheduler/watchdog start/stop, off-cycle run trigger)
  plus two new ones: Positions & Watchlist (live positions from Alpaca
  alongside the discovery candidate universe) and Ticker Detail (a
  per-symbol drill-down — latest decision reasoning, decision history,
  orders — that every ticker mention anywhere in the app now links to).
  A ticker strip pinned to every page polls roughly every 10s for equity,
  heartbeat freshness, and active breaker count; positions and equity
  panels poll roughly every 30s; a past decision's transcript is fetched
  once, since it's immutable, rather than polled. The Overview page is
  composable rather than fixed: a panel registry (`frontend/src/panels/`)
  feeds a drag/resize grid (`react-grid-layout`), with each viewer's
  layout persisted to their own browser's `localStorage` — adding a new
  Overview widget later is one new panel file, nothing else to touch.
  Visual identity is deliberately Bloomberg-terminal-inspired: near-black
  background, amber accent, monospace tabular figures for all numeric
  data (design tokens in `frontend/src/styles/tokens.css`). See
  `docs/superpowers/specs/2026-08-31-dashboard-redesign-design.md` for
  the full design. Write capability still spans process control
  (start/stop, off-cycle run), order cancellation, and config editing
  (candidate universe tickers, risk config fields, and a handful of env
  settings); risk config edits still require a justification note and
  are appended to `run/config_changes.log` for audit. Circuit-breaker
  clearing and the kill switch still remain outside the dashboard.
  Circuit-breaker clearing has its own separate CLI instead (`python -m
  tradingsystem.db.clear_breaker`), deliberately kept outside the
  dashboard so a clear always requires a human-supplied name and review
  note, never a button click. Every write route is still protected by a
  same-origin check (CSRF guard) — not full authentication; the
  dashboard still has no authentication, on the same single-operator,
  localhost-only reasoning as before (on the droplet, "localhost" means
  only someone who can already SSH in as `deploy` can reach it) — but that decision still carries
  more weight than when the dashboard was purely read-only, since an
  unauthenticated client on the machine can start/stop the live trading
  process, cancel orders, and edit risk config. Flagged as a known
  trade-off, not resolved by this plan either. Stopping still honors an
  in-flight research cycle (finishes the current ticker, declines the
  next); a stop that doesn't complete within 60s still reports "still
  running" rather than auto-killing — an explicit separate Force Stop
  action is required to kill immediately, because a forced kill
  mid-order-submission can orphan a live order at the broker with no
  local record. `run/scheduler.log`, `run/watchdog.log`, and
  `run/run_once.log` remain the three log files under the gitignored
  `run/` directory, now viewable via a live-polling log tail on the
  Control page instead of a static dump (stdout/stderr also go to the
  journal: `journalctl -u bot-scheduler`). On the droplet the Control
  page's buttons drive `systemctl` — see "Dashboard process control under
  systemd" in §6.

---

## 8. Testing & go-live criteria

- **No separate backtesting phase** — go straight to paper trading with
  live data. TradingAgents' non-deterministic, LLM-driven reasoning doesn't
  have clean backtest semantics the way a quant signal would; paper trading
  against live data is the more meaningful test.
- **Go-live bar**: minimum **3 months** of paper trading **AND** at least
  one full circuit-breaker trip → manual review → resumption cycle observed
  during that window. Both conditions must be met — calendar time alone is
  not sufficient, since it wouldn't prove the safety mechanisms work under
  real conditions.
- Moving to live capital requires your **explicit written sign-off** after
  that bar is met — not an automatic transition.

---

## 9. Open items — not blocking build, but must be resolved before go-live

- **UK/Alpaca eligibility & tax reporting**: you've confirmed this is
  already checked on your end. Not re-litigated here, but flagging that
  this document doesn't independently verify it.
- **PDT rule transition**: FINRA retired the old PDT rule June 4 2026 in
  favor of a real-time intraday margin framework, but brokers have until
  Oct 2027 to fully transition. Alpaca's current day-trade-count behavior
  needs to be checked against your actual account before go-live — not
  assumed resolved just because the old rule was formally retired.
- **Intended live capital amount**: not set yet (paper balance is $100k;
  live figure to be decided at go-live time, separate from this document).
- **TradingAgents/Ollama live smoke test — completed 2026-08-24 on GPU
  hardware**: after an earlier attempt on the dev laptop (no dedicated GPU,
  CPU-only Ollama) stalled 40+ minutes mid-pipeline without finishing, a full
  live `run_research("AAPL", "2026-08-21")` run completed successfully on the
  actual GPU desktop (RTX 4060 Ti, 8GB VRAM — supersedes the RTX 3060 Ti
  assumed elsewhere in this document) in **22.2 minutes**, returning a
  valid `Underweight`/`sell` decision with full
  reasoning. `ollama ps` during the run showed the model only 73% GPU / 27%
  CPU resident — TradingAgents' 32k-token context window doesn't fully fit
  alongside the model weights in 8GB, which is the likely reason this figure
  is slower than a fully-GPU-resident 7B model would be. This is the data
  point §5's "watch total cycle time as the ticker list grows" note depended
  on: a 3-ticker twice-daily cycle costs roughly an hour of wall time per run
  as currently configured. One Sentiment Analyst structured-output miss
  occurred during the run and fell back to free text automatically
  (TradingAgents' own built-in fallback) — consistent with §5's non-negotiable
  that Ollama tool-calling reliability is materially below cloud models; a
  single occurrence in one run, not yet enough data to say how often this
  recurs across many runs.
- **Ollama Cloud migration — evaluated and adopted, 2026-08-25.** The
  original local-only-inference principle above held until a full 3-ticker
  end-to-end test got killed by an environment timeout roughly 80 minutes
  in, confirming the ~22 min/ticker local figure was impractical for the
  twice-daily/3-ticker cadence running unattended. Reversed the local-only
  principle after explicit evaluation (see
  `docs/superpowers/specs/2026-08-25-ollama-cloud-model-split-design.md`):
  decision-engine inference now runs on Ollama Cloud (§5), proxied through
  the same local `ollama serve` daemon using the signed-in account. Already
  paid for via an existing subscription, so no new cost; local GPU
  inference remains available as a fallback by pointing the two `.env`
  model settings back at local model names, with no code change.
- Two mid-build discoveries also changed assumptions from the original brief
  (both resolved, see the TradingAgents-integration commit): PyPI's
  `tradingagents` package is **not** the real TauricResearch project (a
  same-named, different, unaffiliated package — install from the pinned git
  tag instead, already reflected in `pyproject.toml`); and the real package's
  Portfolio Manager now emits a **5-tier rating** (Buy/Overweight/Hold/
  Underweight/Sell), not 3-way BUY/SELL/HOLD — handled via
  `risk/position_sizing.py`'s conviction-based sizing (Overweight/Underweight
  = half size of Buy/Sell).

---

## 10. What this document does NOT cover yet

Implementation details (exact file/module layout, TradingAgents
integration specifics, Alpaca SDK usage, Postgres schema DDL, Discord
webhook payload format) are deliberately left to the code itself. The
dashboard stack is described in §7, and the droplet deployment (systemd
units in `deploy/systemd/`, `deploy/update.sh`) in §6. Not yet covered
anywhere: schema migrations (there is no Alembic; see §6 "Deploying
updates") and a from-scratch droplet provisioning runbook.

---

## Sign-off

**This build does not proceed to implementation code until you explicitly
confirm this document.** Please review each section above and either:

- Confirm as-is, or
- Flag anything to change before I start building.
