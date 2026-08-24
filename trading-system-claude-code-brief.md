# Autonomous Trading System — Build Brief for Claude Code

Paste this whole document into Claude Code as your opening prompt.

---

## Instruction to Claude Code: do not write any code yet

Before touching a single file, interview me. Work through the question
sections below, a few at a time, in conversation. Don't assume sensible
defaults and move on — trading systems fail quietly when assumptions turn
out to be wrong, so surface every ambiguity. Once you have my answers,
write an `ARCHITECTURE.md` summarizing the design and get my explicit
sign-off before you generate any implementation code.

---

## Project summary (context, not instructions)

I want a system with three parts:

1. **Decision engine** — [TradingAgents](https://github.com/TauricResearch/TradingAgents),
   a multi-agent LLM framework (analysts → bull/bear researchers → trader →
   risk team → portfolio manager) that researches a ticker and returns a
   BUY/SELL/HOLD decision with reasoning. It has native Ollama support
   (`llm_provider: "ollama"`).
2. **Execution layer** — talks to the Alpaca API (paper first) to check
   account/positions/market status and place orders. This is *not* part of
   TradingAgents — TradingAgents' own "simulated exchange" is internal to
   its framework and isn't connected to a real broker. I need a separate
   layer that takes TradingAgents' decision, runs it through pre-trade risk
   validation, and only then calls Alpaca.
3. **Second brain** — a persistent store of every decision, every agent
   debate/reasoning trail, every order and fill, and daily portfolio
   snapshots. This is separate from (and broader than) TradingAgents'
   built-in `trading_memory.md` reflection log. It should let me query
   "why did it buy X on date Y" and "how has it performed since."

All of this needs to run continuously, unattended, with all LLM inference
for the *runtime system* going through my local Ollama instance — not the
Anthropic API. (Claude Code itself, as the tool building this, obviously
uses Claude — that's fine and separate. The deployed trading system is the
part that must be Ollama-only.)

I found a walkthrough that covers the Claude-Code-Routines-plus-Alpaca
shape of this (research → trade → journal, three cron-scheduled prompts,
risk rules in `CLAUDE.md`, script-level order validation, a critic-agent
pattern). That pattern is worth borrowing for the *risk-control layering*
and *journal structure*, but its scheduling mechanism (Claude Code headless
sessions on a cron) bills through the Anthropic API per run, which conflicts
with running everything through Ollama. **Flag this to me explicitly in the
interview** — I likely want a standalone Python scheduler (cron/systemd
timer/APScheduler) that calls Ollama directly, with Claude Code used only
as the dev tool that builds and maintains the codebase, not as the runtime
orchestrator. Confirm this with me rather than assuming it.

---

## Non-negotiable engineering principles

Bake these in regardless of my answers below — don't ask permission to
include them, ask if I want to adjust the specific numbers:

- **Paper trading is the default and only mode until I explicitly say
  otherwise**, in writing, after a defined evaluation period.
- **Separation of judgment and execution.** The LLM (TradingAgents) decides
  *what it wants to do*. A separate, deterministic, non-LLM validation
  function is the only thing allowed to actually call Alpaca's order
  endpoint. It re-checks position size limits, cash reserve, exposure caps,
  and market status independent of whatever the agent claims — never trust
  the agent's own account of its risk math.
- **Kill switch as an independent process/mechanism** — not a flag the
  agent checks inside its own loop, since a broken agent can't be trusted
  to check its own kill switch. A file, a systemd unit, or an external
  monitor that can halt order placement even if the main process is stuck.
- **Circuit breakers, not just limits**: auto-pause on daily drawdown
  (e.g. 3–5%), weekly drawdown (e.g. 8–12%), and require a full manual
  review to resume after either trips. Numbers are a starting point to
  confirm with me, not gospel.
- **Limit orders only**, never market orders, enforced in code — not just
  in a prompt instructing the agent to behave.
- **Market-status check is the first action of every single run**, before
  any research or trading logic executes.
- **Every run logs a journal entry, including no-action days.** If we
  don't know why the agent did or didn't do something, we can't debug it.
- **Heartbeat monitoring** — the scheduler should be independently
  observable as "still alive" so a silent crash doesn't look like "no
  trades needed today."
- **Ollama tool-calling reliability is materially below cloud models**
  (commonly cited around 70–90% valid/complete tool calls vs. 95%+ for
  frontier hosted models, model-dependent). Treat every structured output
  from the local model as untrusted input: validate schema, retry on
  malformed output, and fail closed (no trade) rather than fail open on
  ambiguous output. Pick a model with confirmed native tool-calling support
  in Ollama (this needs to be part of the interview, not assumed).
- **Secrets never hardcoded.** `.env`, gitignored, separate paper/live
  Alpaca keys kept in clearly distinct places so a config mistake can't
  silently point paper logic at a live key.
- **Long-only, equities/ETFs, no options, no shorting** unless I say
  otherwise in the interview — both add risk-model complexity this system
  isn't scoped for yet.
- TradingAgents' own README is explicit that it's a research framework,
  performance is non-deterministic across runs, and it is **not** financial
  advice. Build accordingly — this is a tool for executing and studying a
  defined process, not a system anyone should assume is profitable.

---

## Interview: work through these with me before designing anything

### 1. Scope & watchlist
- Which tickers/asset classes to start with, and roughly how many? (Fewer,
  liquid, well-covered names are easier to research well.)
- Equities/ETFs only, or crypto too? (Crypto trades 24/7; equities don't —
  this materially changes what "runs 24/7" means in practice.)
- Any sectors or names I specifically want included or excluded?

### 2. Capital, account & regulatory context
- Paper account balance to simulate against, and (later) intended live
  capital?
- Confirm Alpaca account type/eligibility — I'm UK-based; Alpaca is a US
  broker, so confirm account eligibility and any tax/reporting
  implications are things I've checked myself, not something to assume.
- Note: FINRA's PDT rule was retired June 4 2026 in favor of a real-time
  intraday margin framework, but brokers have until Oct 2027 to fully
  transition — don't assume old day-trade-count limits no longer apply at
  all; confirm current Alpaca account behavior before relying on it.

### 3. Risk controls
- Confirm or adjust: max % of portfolio per position, cash reserve %,
  daily/weekly drawdown circuit breaker thresholds, stop-loss rule.
- Who/what can clear a tripped circuit breaker — must it be me manually?
- Should there be a review-required gate on every trade at first (a
  human-in-the-loop approval step before any live order), separate from
  the paper-trading gate — i.e., even in paper mode, do I want to approve
  trades initially, or let it run fully unattended in paper from day one?

### 4. TradingAgents / decision engine configuration
- `max_debate_rounds` and research depth — deeper debate costs more local
  inference time; what's an acceptable run time per ticker per session
  given local hardware?
- Which analyst types matter most to me (fundamentals, sentiment, news,
  technical) — any to weight more heavily or drop?
- How often should the full research pipeline run per ticker — once a day,
  more?

### 5. Local model setup (Ollama)
- What hardware/VRAM is available for Ollama? This determines realistic
  model choice.
- Confirmed native tool-calling models to consider (subject to what's
  actually pulled and tested on the hardware) — pick and justify one,
  don't assume.
- Same model for every agent role, or a larger model for the trader/risk
  manager and a smaller/faster one for individual analysts?

### 6. Second brain / memory system
- Structured store: SQLite (simplest, fine for single-machine 24/7) vs.
  Postgres (if I want networked/multi-client access)?
- Do I want semantic search over past reasoning (a vector store) or is a
  relational schema with good indexing enough for now?
- What exactly gets persisted: full agent debate transcripts, just final
  decisions + reasoning summary, order/fill data, daily portfolio
  snapshots, realized P&L per trade — confirm the schema before building it.
- Should it wrap/ingest TradingAgents' own `trading_memory.md` log, or run
  fully independently?

### 7. Execution & order management
- Confirm the validation layer's exact checks (position size, cash
  reserve, total exposure, stale-data check, duplicate-order check) before
  it's built.
- How to handle partial fills, rejected orders, and Alpaca API errors —
  retry policy, alerting, or just log and skip?

### 8. Scheduling & runtime architecture
- Confirm: standalone Python daemon/scheduler (not Claude Code Routines)
  calling Ollama directly, per the note above — or is there a reason to
  reconsider?
- Cron, systemd timers, or an in-process scheduler (APScheduler)?
- Does research run separately from trade execution (like the MindStudio
  three-stage split: research → trade → journal), or combined per ticker?

### 9. Monitoring, alerting & human oversight
- How do I want to be notified: email digest, Discord/Slack webhook,
  something else?
- What triggers an immediate alert vs. a daily summary — circuit breaker
  trips should probably be immediate; a routine no-action day shouldn't be.
- Do I want a simple dashboard/UI over the second brain, or is querying the
  DB/reading journal files enough for now?

### 10. Testing, backtesting & go-live criteria
- What counts as "proven enough" to move from paper to live — a time
  period, a performance bar, a number of complete drawdown-recovery
  cycles, all of the above?
- Any backtesting before paper trading even starts, or straight to paper
  with live data?

### 11. Deployment environment
- What machine runs this 24/7 — my own always-on hardware, a home server,
  something else? (Needs to be wherever Ollama already runs.)
- Uptime/restart behavior if the machine reboots or Ollama itself crashes?

---

## After the interview

Write `ARCHITECTURE.md` covering: component diagram, data flow (research →
decision → validation → execution → journal), the second-brain schema, the
scheduling design, the full list of risk controls with my confirmed
numbers, and the go-live criteria we agreed on. Get my explicit sign-off on
that document before generating implementation code.
