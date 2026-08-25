# Ollama Cloud Model Split — Design Spec

Status: **Approved by user 2026-08-25. Ready for direct implementation (no plan doc — change is small enough to skip subagent-driven-development).**

This spec covers switching the decision engine's model configuration from a
single shared model to two independently-configurable models — one for
"deep think" roles (Research Manager, Trader, Risk Judge, Portfolio Manager)
and one for "quick think" roles (the four analysts) — and pointing both at
Ollama Cloud models by default, while keeping local Ollama fully supported
as a switchable fallback.

---

## 1. Background and what changed since ARCHITECTURE.md

ARCHITECTURE.md §5 originally rejected per-role model splitting specifically
because local 8GB VRAM can only keep one model resident at a time — reload
latency between roles would be worse than one shared model. §9's amendment
(added 2026-08-24) named Ollama Cloud as the option to revisit if local
performance proved limiting; the 2026-08-24 end-to-end test (three tickers,
real Ollama + real paper Alpaca) confirmed a real operational problem: the
run was killed by an external timeout partway through the third ticker,
after ~80 minutes for two tickers.

The user already pays a **flat-rate** Ollama Cloud plan for another project
(no marginal per-token cost for this system), which removes the cost
objection that made local-only the right call originally. Per-role model
splitting stops being a bad idea once the model doesn't have to fit in this
machine's VRAM.

**How Ollama Cloud actually works** (verified during this session, not
assumed): a model name suffixed `:cloud` (e.g. `gpt-oss:120b-cloud`) is
detected by the *local* `ollama serve` daemon, which proxies the request to
Ollama's cloud infrastructure using the machine's signed-in account — no
separate API key or base URL. TradingAgents' `ollama` provider already
talks to `http://localhost:11434/v1` unchanged; only the model name string
differs between local and cloud. **`ollama serve` must still be running and
signed in** on whichever machine the scheduler runs on — this is "cloud
inference via a required local proxy," not a fully local-machine-free setup.

**Structured output verified compatible**: Ollama Cloud does not support
Ollama's native JSON-schema `format` parameter, but TradingAgents' capability
table (`llm_clients/capabilities.py`) uses `method="function_calling"`
(tool-calling) for all Ollama-provider models by default — a mechanism
Ollama Cloud models support with documented parity to OpenAI's API. This was
checked directly against the installed package source, not assumed.

---

## 2. Models chosen

- **Deep-think** (Research Manager, Trader, Risk Judge, Portfolio Manager):
  `gpt-oss:120b-cloud`
- **Quick-think** (Market/Sentiment/News/Fundamentals analysts):
  `nemotron-3-super:cloud`

Chosen from a shortlist researched against TradingAgents' actual
architecture and one direct Ollama Cloud speed/reliability benchmark (not
generic coding leaderboards). Speed/quality data for cloud models is
informal and workload-specific — validate with a real timed run after
switching, same as the local baseline was validated on 2026-08-24.

---

## 3. Config changes

`src/tradingsystem/config.py`: replace the single `tradingagents_model`
field with two, so the split is a first-class, independently-documented
setting rather than a naming convention buried in `.env`:

```python
tradingagents_deep_think_model: str = "qwen2.5:7b-instruct"
tradingagents_quick_think_model: str = "qwen2.5:7b-instruct"
```

Defaults stay local-model names (safe for a fresh clone/test run with no
Ollama Cloud account signed in) — the real operational values live in
`.env`, consistent with every other environment-specific setting in this
project.

`.env` and `.env.example` both updated: `TRADINGAGENTS_MODEL=...` (one line)
replaced with two lines, `TRADINGAGENTS_DEEP_THINK_MODEL` and
`TRADINGAGENTS_QUICK_THINK_MODEL` — `.env` gets the real cloud model names
chosen above, `.env.example` documents both with a comment explaining the
role split and that either can point at a local or `:cloud`-suffixed model
name to switch backends. This is the "change it easily later" requirement —
picking a different model when a better one ships is a one-line `.env` edit,
no code change.

`src/tradingsystem/decision_engine/ta_config.py`: `build_ta_config` sets
`config["deep_think_llm"]` and `config["quick_think_llm"]` from the two new
settings instead of both from the one old setting. `backend_url` is
unchanged (`settings.ollama_base_url`, still `http://localhost:11434/v1`) —
no new base-URL or API-key config needed, per §1's proxy mechanism.

---

## 4. What this does NOT cover

No code changes to `runner.py`, `cycle.py`, or any orchestration module —
this is purely a config/model-selection change, the rest of the pipeline is
backend-agnostic already. No change to `ARCHITECTURE.md`'s §9 amendment
wording itself (that already anticipated this move); a short follow-up note
recording that the switch happened and why belongs there, not a rewrite of
the non-negotiable's framing. No production 24/7 host decision — still
deferred, unrelated to this change. No rate-limit/usage-cap handling for
Ollama Cloud's session/weekly caps — noted as a future watch item, not
solved here.

## 5. Testing

No existing test references `tradingagents_model` directly (checked via
grep across the repo) — the field rename requires no test updates. Manual
validation: run the same kind of live smoke test used on 2026-08-24
(`run_research` for one ticker) against the new cloud models before trusting
them for a full multi-ticker run, to confirm the model names resolve
correctly and structured-output/tool-calling behaves as expected in
practice, not just in theory.
