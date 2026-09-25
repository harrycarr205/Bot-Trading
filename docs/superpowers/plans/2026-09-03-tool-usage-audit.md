# Tool Usage Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find out empirically whether the News Analyst's macro/prediction-market tools are actually being invoked on live runs (not just wired into the graph), by attaching a lightweight audit callback to every TradingAgents run and logging what got requested per `AgentRun`.

**Architecture:** `TradingAgentsGraph.__init__` already accepts an optional `callbacks: list | None` parameter (`.venv/Lib/site-packages/tradingagents/graph/trading_graph.py:73`, forwarded into every LLM client's constructor at line 99) — this is a supported extension point in the pinned package, not something we're patching. A `langchain_core.callbacks.BaseCallbackHandler` subclass bound there fires on every LLM call across the whole graph (all four analysts, both researchers, the trader, all three risk debators, and the portfolio manager share the same two LLM instances), so inspecting each `AIMessage.tool_calls` on `on_llm_end` tells us which tool names the model actually asked to call, per run — without touching the vendored package. `decision_engine/runner.py:run_research()` wires this in and logs the result once a run succeeds.

Separately, reading the pinned package's source turned up a concrete, verifiable finding (Task 3): `get_insider_transactions` is registered in the `"news"` `ToolNode` (`trading_graph.py:214`) but is **not** included in `news_analyst.py`'s own `tools` list that gets passed to `llm.bind_tools(tools)` (`news_analyst.py:20-25`) — so the News Analyst's LLM is never even offered that tool as callable. This is not a reliability question (the earlier callback answers that for the tools that *are* offered); it's a structural gap in the pinned v0.3.1 package. This plan documents it precisely and explicitly defers the fix (which requires patching the external dependency) rather than silently forking it.

**Tech Stack:** Python 3.11, `langchain_core` 1.6 (already a transitive dependency via `tradingagents`), pytest, `caplog`.

**Spec:** The Alpha Backlog (idea #04, "Find out whether your macro and insider-trading data are actually being used"), published artifact `https://claude.ai/code/artifact/58853951-b14c-4bfd-9d7e-97d14c43b033`. This plan document is self-contained; the artifact is provenance only.

## Global Constraints

- Do not modify anything under `.venv/Lib/site-packages/tradingagents/` — pinned via `tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git@v0.3.1` (`pyproject.toml:26`); any local edit is discarded on the next `pip install`.
- `ALPHA_VANTAGE_API_KEY` and `FRED_API_KEY` are both confirmed provisioned in the real `.env` as of 2026-09-03 (checked non-destructively — presence only, values not read). `get_macro_indicators` (FRED-backed) and `get_prediction_markets` (Polymarket, no key required) can therefore return real data; this audit is meaningful now in a way it would not have been when `ARCHITECTURE.md:298-305` was written (that note also predates the later migration from a local `qwen2.5:7b-instruct` model to Ollama Cloud's `gpt-oss:120b-cloud`/`nemotron-3-super:cloud`, per `ARCHITECTURE.md:142-155` — tool-calling reliability may look meaningfully different on the current models than it did when that caveat was written; don't assume either way, that's what this audit is for).
- The audit callback must add no new failure mode to `run_research()` — if callback bookkeeping ever raised, it must not turn a successful TradingAgents run into a failed one. `BaseCallbackHandler` methods here are simple list appends with no I/O, so this is inherent, but keep it that way (no DB writes, no network calls inside the callback itself).

---

### Task 1: Build the tool-call audit callback

**Files:**
- Create: `src/tradingsystem/decision_engine/tool_audit.py`
- Test: `tests/test_tool_audit.py`

**Interfaces:**
- Consumes: `langchain_core.callbacks.BaseCallbackHandler`, `langchain_core.outputs.LLMResult`.
- Produces: `ToolCallAuditCallback` — a class with `.requested_tool_names: list[str]` (populated in call order, duplicates kept) and `.on_llm_end(response: LLMResult, **kwargs) -> None`. Task 2 constructs one instance per attempt and reads `.requested_tool_names` after a successful run.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tool_audit.py`:

```python
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from tradingsystem.decision_engine.tool_audit import ToolCallAuditCallback


def make_llm_result(tool_calls: list[dict]) -> LLMResult:
    message = AIMessage(content="", tool_calls=tool_calls)
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_records_nothing_when_no_tool_calls_requested():
    callback = ToolCallAuditCallback()
    callback.on_llm_end(make_llm_result([]))
    assert callback.requested_tool_names == []


def test_records_a_single_tool_call_name():
    callback = ToolCallAuditCallback()
    callback.on_llm_end(
        make_llm_result([{"name": "get_macro_indicators", "args": {}, "id": "call_1"}])
    )
    assert callback.requested_tool_names == ["get_macro_indicators"]


def test_records_tool_calls_across_multiple_llm_end_events_in_order():
    callback = ToolCallAuditCallback()
    callback.on_llm_end(make_llm_result([{"name": "get_news", "args": {}, "id": "call_1"}]))
    callback.on_llm_end(
        make_llm_result([{"name": "get_prediction_markets", "args": {}, "id": "call_2"}])
    )
    assert callback.requested_tool_names == ["get_news", "get_prediction_markets"]


def test_plain_text_final_answer_records_nothing():
    # The graph's final "FINAL TRANSACTION PROPOSAL: **BUY**" message has no
    # tool_calls at all — must not raise or record a phantom entry.
    callback = ToolCallAuditCallback()
    message = AIMessage(content="FINAL TRANSACTION PROPOSAL: **BUY**")
    callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
    assert callback.requested_tool_names == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradingsystem.decision_engine.tool_audit'`.

- [ ] **Step 3: Write the implementation**

Create `src/tradingsystem/decision_engine/tool_audit.py`:

```python
"""Audits which optional tools TradingAgents' agents actually request per run.

ARCHITECTURE.md documents Ollama tool-calling reliability as a live concern —
a tool being wired into an analyst's `tools` list (e.g. news_analyst.py) does
not guarantee the model ever calls it. TradingAgentsGraph accepts an optional
`callbacks` list (graph/trading_graph.py) forwarded into every LLM client it
builds; those two LLM instances (deep-think, quick-think) are shared across
every node in the graph, so a callback bound here fires on every single LLM
call in the run — no changes to the pinned tradingagents package needed.
"""

from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


class ToolCallAuditCallback(BaseCallbackHandler):
    """Collects every tool name any LLM call in the graph requested, in order."""

    def __init__(self) -> None:
        self.requested_tool_names: list[str] = []

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        for generation_list in response.generations:
            for generation in generation_list:
                message = getattr(generation, "message", None)
                for tool_call in getattr(message, "tool_calls", None) or []:
                    self.requested_tool_names.append(tool_call["name"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_audit.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/decision_engine/tool_audit.py tests/test_tool_audit.py
git commit -m "feat: add tool-call audit callback for TradingAgents runs"
```

---

### Task 2: Wire the audit into `run_research()` and log per-run results

**Files:**
- Modify: `src/tradingsystem/decision_engine/runner.py:12-133`
- Modify: `tests/test_decision_engine_runner.py:34-46` (the `ScriptedGraph` test double)
- Modify: `tests/test_cycle.py:101-111` (a **second, separate** `ScriptedGraph` test double — `test_cycle.py` does not import the one in `test_decision_engine_runner.py`; it defines its own copy and monkeypatches it over `runner_module.TradingAgentsGraph` the same way)
- Test: `tests/test_decision_engine_runner.py`, `tests/test_cycle.py`

**Interfaces:**
- Consumes: `ToolCallAuditCallback` from Task 1.
- Produces: `run_research()`'s behavior is unchanged (same `ResearchResult` return shape) except it now logs one `INFO`-level line per successful attempt: `"tool calls requested for %s (agent_run=%s): %s"` with `ticker`, `agent_run.id`, and the sorted-unique list of requested tool names.

- [ ] **Step 1: Write the failing test**

`ScriptedGraph` in `tests/test_decision_engine_runner.py` currently declares `def __init__(self, debug=False, config=None): pass` (line 39) — it will reject the new `callbacks=` kwarg `run_research()` is about to start passing. Update it first so the existing tests keep passing, then add the new assertion.

Replace `tests/test_decision_engine_runner.py:34-46`:

```python
class ScriptedGraph:
    """Stand-in for TradingAgentsGraph — scripted per-call outcomes, no real Ollama call."""

    calls = []

    def __init__(self, debug=False, config=None, callbacks=None):
        self.callbacks = callbacks or []

    def propagate(self, ticker, trade_date):
        outcome = ScriptedGraph.calls.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
```

Make the identical change to `test_cycle.py`'s own `ScriptedGraph` (`tests/test_cycle.py:101-111`) — same class body, same fix, different file:

```python
class ScriptedGraph:
    calls = []

    def __init__(self, debug=False, config=None, callbacks=None):
        pass

    def propagate(self, ticker, trade_date):
        outcome = ScriptedGraph.calls.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
```

Append a new test at the end of `tests/test_decision_engine_runner.py`:

```python
def test_logs_requested_tool_names_on_success(db_session, monkeypatch, caplog):
    ScriptedGraph.calls = [(make_final_state(), "Buy")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)

    settings = Settings(tradingagents_run_max_attempts=2)
    with caplog.at_level("INFO", logger=runner_module.__name__):
        result = run_research(db_session, "AAPL", "2026-01-05", market_status="open", settings=settings)

    assert result.ok
    assert any("tool calls requested for AAPL" in record.message for record in caplog.records)
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `pytest tests/test_decision_engine_runner.py -v`
Expected: the three pre-existing tests still PASS (ScriptedGraph's new `callbacks=None` default doesn't break them); `test_logs_requested_tool_names_on_success` FAILS — no such log line is emitted yet.

- [ ] **Step 3: Wire the callback into `run_research()`**

In `src/tradingsystem/decision_engine/runner.py`, add the import:

```python
from tradingsystem.decision_engine.tool_audit import ToolCallAuditCallback
```

Replace the attempt loop body (lines 97-112) with:

```python
    for attempt in range(1, settings.tradingagents_run_max_attempts + 1):
        tool_audit = ToolCallAuditCallback()
        try:
            graph = TradingAgentsGraph(debug=False, config=config, callbacks=[tool_audit])
            final_state, rating = graph.propagate(ticker, trade_date)
        except Exception as exc:  # noqa: BLE001 - graph/langchain failures aren't consistently typed
            last_error = exc
            log.warning("TradingAgents run failed for %s (attempt %d): %s", ticker, attempt, exc)
            continue

        if rating not in RATING_VALUES:
            last_error = ValueError(f"unexpected rating {rating!r}, not in {RATING_VALUES}")
            log.warning(
                "TradingAgents returned an unrecognized rating for %s (attempt %d): %r",
                ticker, attempt, rating,
            )
            continue

        log.info(
            "tool calls requested for %s (agent_run=%s): %s",
            ticker, agent_run.id, sorted(set(tool_audit.requested_tool_names)),
        )

        agent_run.finished_at = datetime.datetime.utcnow()
```

(The rest of the successful-attempt block — `agent_run.outcome = "decision_recorded"` through the `return ResearchResult(...)` — is unchanged; only the new `log.info(...)` call is inserted immediately before the existing `agent_run.finished_at = ...` line.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_decision_engine_runner.py tests/test_cycle.py -v`
Expected: all tests in both files PASS (5 in `test_decision_engine_runner.py`; `test_cycle.py`'s count is unchanged from before this task, just no longer erroring on the `callbacks` kwarg).

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest -v`
Expected: no new failures. (Both `ScriptedGraph` signature changes are additive — `callbacks=None` — so nothing else that constructs either one should break.)

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/decision_engine/runner.py tests/test_decision_engine_runner.py tests/test_cycle.py
git commit -m "feat: log which optional tools TradingAgents actually requested per run"
```

---

### Task 3: Document the `get_insider_transactions` wiring gap and defer the fix decision

This is a documentation-only task. Patching `news_analyst.py` requires modifying the pinned external `tradingagents` package (`graph/setup.py:13-17` imports `create_news_analyst` by name at module load time, so even a runtime monkeypatch of `tradingagents.agents.analysts.news_analyst.create_news_analyst` would not reach `graph/setup.py`'s already-bound reference — the only reliable patch point is `tradingagents.graph.setup.create_news_analyst` itself, or a maintained fork). That is a materially bigger, riskier change than "audit whether tools are used," and the backlog itself labels the fix "medium" effort versus "low" for the audit — so this task documents the finding precisely and leaves the fix as an explicit, deferred decision rather than taking it silently.

**Files:**
- Modify: `ARCHITECTURE.md` (append near the existing decision-engine section, `ARCHITECTURE.md:140-165`)

**Interfaces:** none — documentation only.

- [ ] **Step 1: Add the finding to `ARCHITECTURE.md`**

Append this block after the existing `## 5. Decision engine configuration` section content (after line 165, before the `---` separator at line 166):

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add ARCHITECTURE.md
git commit -m "docs: record the get_insider_transactions wiring-gap finding"
```

---

## Self-Review

**1. Spec coverage:** Idea #04 asks to (a) find out whether macro/insider tools are actually used and (b) treat this as investigative first, fix-if-warranted second. Task 1+2 build and wire the empirical audit (answers (a) for the tools that are reachable at all). Task 3 documents the one thing that turned out to be a structural gap rather than a reliability question, and explicitly defers rather than silently patches a pinned dependency (matches (b) and the "medium to fix" effort label from the backlog).

**2. Placeholder scan:** No TBDs. Task 3's deferred fix is a documented decision point, not an unfinished implementation.

**3. Type consistency:** `ToolCallAuditCallback.requested_tool_names: list[str]` (Task 1) is read as-is in Task 2's `log.info` call — no signature drift. Both `ScriptedGraph` test doubles (`test_decision_engine_runner.py` and the separate copy in `test_cycle.py`) are updated identically in Task 2, since `runner.py`'s new `callbacks=` kwarg would otherwise break whichever one wasn't caught.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-03-tool-usage-audit.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
