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
in-process, running as a systemd service) that calls Ollama directly.
**Claude Code is a dev-tool only** — it builds and maintains this codebase
but is never in the runtime path and never bills per trading run. This was
explicitly confirmed rather than assumed, per the brief's flag.

---

## 2. Data flow (per scheduled run)

Three-stage split, run twice daily (pre-market and midday) per ticker:

1. **Market status check** — always first, before anything else. If market
   closed/unavailable, log a journal entry and stop.
2. **Research stage** — TradingAgents runs its full pipeline (fundamentals,
   sentiment, news, technical analysts → bull/bear researchers → trader →
   risk team → portfolio manager) via Ollama (Qwen2.5:7b-instruct), 1 debate
   round. Produces a decision (BUY/SELL/HOLD) + reasoning + full debate
   transcript. Every structured output is schema-validated; malformed
   output triggers a retry, and persistent failure fails closed (no trade).
3. **Trade stage** — the decision is handed to the deterministic risk
   validation layer (see §4). This layer — not TradingAgents — is the only
   code path allowed to call Alpaca's order endpoint. It independently
   re-derives position size, cash reserve, and exposure; it does not trust
   any risk math the agent claims to have already done.
4. **Journal stage** — every run, including no-action/HOLD days and days
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
| Watchlist | AAPL, MSFT, SPY to start (equities/ETFs only, long-only, no options/shorting) — freely editable config, not hardcoded; see note below |
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

**Watchlist is a config list, not hardcoded.** Nothing in TradingAgents'
per-ticker pipeline or the risk validation layer's portfolio-%-relative
checks ties the system to exactly AAPL/MSFT/SPY. Adding or removing a
ticker is a config file/DB table edit, taking effect on the next scheduled
run — no code change required. You're responsible for your own judgment
calls on a new ticker's liquidity/coverage before adding it; the system
doesn't gate this automatically (chosen over a lightweight run-time-budget
check, to keep the mechanism simple). Worth remembering: each additional
ticker adds real local-inference time per run (more Ollama calls per
cycle), so watch total cycle time as the list grows.

---

## 5. Decision engine configuration

- Model: **Qwen2.5:7b-instruct** via Ollama, same model resident for every
  agent role (analysts, bull/bear researchers, trader, risk manager) — your
  8GB VRAM (RTX 3060 Ti) realistically keeps one ~7-8B model resident at a
  time, so role-specific model swapping was rejected to avoid reload
  latency and to keep tool-calling reliability validation to one model.
- `max_debate_rounds`: 1 (shallow, to validate the full pipeline end-to-end
  first; can be increased once run time and quality are assessed).
- Analysts: all four (fundamentals, sentiment, news, technical), equally
  weighted — TradingAgents' default full pipeline.
- Run cadence: **twice daily per ticker**, pre-market and midday.
- Every structured output from Qwen2.5 is treated as untrusted: schema
  validated, retried on malformed output, fails closed (no trade) on
  persistent ambiguity — per the brief's non-negotiable on local
  tool-calling reliability.

---

## 6. Scheduling & runtime architecture

- Standalone Python process using **APScheduler** (in-process, not OS-level
  cron) — chosen so circuit-breaker state and heartbeat status can be kept
  in memory within one coherent process rather than reconstructed from
  external state on every cron invocation.
- Runs as a **systemd service**: `Restart=on-failure`, enabled on boot.
  Survives machine reboot and Ollama crashes without manual intervention.
- Runs on **this machine** (the RTX 3060 Ti box), where Ollama is already
  hosted.
- Kill switch is an independent mechanism — not a flag the agent checks in
  its own loop — implemented as an external file/systemd-unit check that
  the validation layer consults before every order, so a stuck/broken agent
  process can't bypass it.

---

## 7. Monitoring & alerting

- **Discord webhook** for notifications.
- **Immediate alerts** on: circuit breaker trips, order rejections/API
  errors, scheduler heartbeat failure, and every trade placed (full
  visibility, not just failures).
- **Simple dashboard** over the second brain is in scope (added during
  interview, beyond the brief's original "DB queries are enough" framing) —
  a local web UI over decisions, orders/fills, P&L, and journal entries.

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
  assumed elsewhere in this document; same 8GB VRAM class, so the
  one-7-8B-model-resident capacity reasoning in §5 still holds) in **22.2
  minutes**, returning a valid `Underweight`/`sell` decision with full
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
- **Local-only Ollama inference — open to revisiting if performance proves
  limiting.** The non-negotiable above (all runtime LLM inference stays
  local, no cloud billing/dependency in the trading loop) still holds. The
  ~22 min/ticker figure and the 27% CPU-offload finding above are the
  concrete trigger this bullet exists for: if the twice-daily/3-ticker pace
  proves impractical once the scheduler is actually running unattended,
  moving some or all inference to a paid, hosted Ollama Cloud endpoint is the
  option to revisit — explicitly evaluated then, not defaulted into now,
  since it reverses the local-only principle above and needs its own
  cost/reliability/data-handling discussion at that point.
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
integration specifics, Alpaca SDK usage, Postgres schema DDL, systemd unit
files, Discord webhook payload format, dashboard tech choice) are
deliberately left for the implementation phase, once this architecture is
signed off.

---

## Sign-off

**This build does not proceed to implementation code until you explicitly
confirm this document.** Please review each section above and either:

- Confirm as-is, or
- Flag anything to change before I start building.
