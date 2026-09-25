# Debate Rounds Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise the TradingAgents investment-debate depth from one bull/bear round to two, as a deliberately-tracked experiment rather than a silent config bump — since the evidence for "more debate = better" is genuinely mixed (see Global Constraints).

**Architecture:** `tradingagents_max_debate_rounds` is already a first-class `Settings` field (`src/tradingsystem/config.py:51`), read by `build_ta_config()` (`src/tradingsystem/decision_engine/ta_config.py:22`) into the TradingAgents config dict, and consumed by the pinned `TradingAgentsGraph`'s `ConditionalLogic` to gate how many bull/bear rounds run before the Research Manager judges. The value is *not* currently overridden in the real `.env` (confirmed empty), so it is currently taking the class default of `1`. This plan changes only that one default — `max_risk_discuss_rounds` (aggressive/conservative/neutral debate) stays at `1`, deliberately, so a later before/after comparison isn't confounded by two knobs moving at once.

**Tech Stack:** Python 3.11, pydantic-settings, pytest. No changes to the pinned `tradingagents` package.

**Spec:** The Alpha Backlog (idea #07, "Try two debate rounds instead of one — and watch for the trap"), published artifact `https://claude.ai/code/artifact/58853951-b14c-4bfd-9d7e-97d14c43b033`. This plan document is self-contained; the artifact is provenance only.

## Global Constraints

- Do not change `tradingagents_max_risk_discuss_rounds` — this experiment isolates the investment-debate round count only.
- Do not modify anything under `.venv/Lib/site-packages/tradingagents/` — it is a pinned external dependency (`tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git@v0.3.1` in `pyproject.toml:26`) and any local edit there is silently discarded on the next `pip install`.
- A cited empirical source in the backlog found a third debate round shows "clearly diminishing returns" — this plan goes to 2 rounds, not 3.
- A separate 2026 benchmark cited in the backlog found the simplest architecture (single agent, good memory) beat more elaborate multi-role debate setups on both return and Sharpe in that test — this change is explicitly an experiment to watch, not an assumed improvement. Task 2 below is the mechanism for watching it; do not skip it.

---

### Task 1: Bump the default debate-round count

**Files:**
- Modify: `src/tradingsystem/config.py:48-51`
- Modify: `.env.example:32`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Settings().tradingagents_max_debate_rounds == 2` (previously `1`). No other module's interface changes — `build_ta_config()` and everything downstream already read this field by name.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py` (same file/style as the existing `test_discovery_slots_per_cycle_default`):

```python
def test_max_debate_rounds_default_is_two():
    # _env_file=None isolates this from whatever the real repo .env currently
    # sets TRADINGAGENTS_MAX_DEBATE_ROUNDS to, same reasoning as the
    # discovery-slots test above.
    assert Settings(_env_file=None).tradingagents_max_debate_rounds == 2


def test_max_risk_discuss_rounds_unchanged_at_one():
    # Deliberately not raised alongside max_debate_rounds — see the debate
    # rounds experiment plan (docs/superpowers/plans/2026-09-03-debate-rounds-experiment.md):
    # changing both at once would confound which knob caused any observed effect.
    assert Settings(_env_file=None).tradingagents_max_risk_discuss_rounds == 1
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `pytest tests/test_config.py -v`
Expected: `test_max_debate_rounds_default_is_two` FAILS (`assert 1 == 2`); `test_max_risk_discuss_rounds_unchanged_at_one` PASSES already.

- [ ] **Step 3: Change the default and update its comment**

In `src/tradingsystem/config.py`, replace lines 48-51:

```python
    # ARCHITECTURE.md §5: shallow, single-round debate to validate the
    # pipeline end-to-end first. Not part of risk_config.yaml — that file is
    # specifically the risk *validation* numbers, not decision-engine depth.
    tradingagents_max_debate_rounds: int = 1
    tradingagents_max_risk_discuss_rounds: int = 1
```

with:

```python
    # Bumped from 1 -> 2 on 2026-09-03 as a tracked experiment (see
    # docs/superpowers/plans/2026-09-03-debate-rounds-experiment.md): a second
    # bull/bear round surfaced genuinely new evidence ~60% of the time in one
    # practitioner's tuning, but a separate benchmark found more debate isn't
    # automatically better decision quality. max_risk_discuss_rounds is
    # deliberately left at 1 so this isolates one variable. Not part of
    # risk_config.yaml — that file is specifically the risk *validation*
    # numbers, not decision-engine depth.
    tradingagents_max_debate_rounds: int = 2
    tradingagents_max_risk_discuss_rounds: int = 1
```

- [ ] **Step 4: Update `.env.example` for documentation consistency**

In `.env.example`, change line 32 from:

```
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
```

to:

```
TRADINGAGENTS_MAX_DEBATE_ROUNDS=2
```

(The real `.env` does not currently set this key, so it already inherits the `Settings` default — no edit needed there. If a later comparison wants to force it back to 1 without a code change, set `TRADINGAGENTS_MAX_DEBATE_ROUNDS=1` in `.env` directly.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/config.py .env.example tests/test_config.py
git commit -m "feat: raise TradingAgents debate rounds 1->2 as a tracked experiment"
```

---

### Task 2: Manual watch checklist (documentation, not code)

This idea's own evidence is mixed — the plan's job is to make sure someone actually looks, not to build new instrumentation for a change this small (the backlog rates this "Effort: very low"; a bespoke analytics script would be scope creep on a one-line config change).

**Files:**
- Modify: `ARCHITECTURE.md` — add a short dated note near the file's existing `max_debate_rounds`/decision-engine documentation (search for "max_debate_rounds" first; add adjacent to any existing mention, otherwise append to the end of the decision-engine section).

- [ ] **Step 1: Add the review checklist to `ARCHITECTURE.md`**

Append this block near the existing `max_debate_rounds` documentation in `ARCHITECTURE.md`:

```markdown
### Debate-rounds experiment (2026-09-03)

`tradingagents_max_debate_rounds` raised 1 -> 2. This is a tracked
experiment, not an assumed improvement — review after ~2 weeks of live
cycles:

1. Query recent `debate_transcripts` rows with `role IN ('bull_researcher', 'bear_researcher')`
   grouped by `agent_run_id`, ordered by `created_at`. Each `agent_run_id`
   should now show two bull entries and two bear entries instead of one.
2. Read a sample of the second-round entries. Do they cite evidence not
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
```

- [ ] **Step 2: Commit**

```bash
git add ARCHITECTURE.md
git commit -m "docs: add review checklist for the debate-rounds experiment"
```

---

## Self-Review

**1. Spec coverage:** Idea #07 asks for (a) raising debate rounds as an experiment and (b) watching transcripts rather than treating it as fire-and-forget. Task 1 covers (a); Task 2 covers (b). `max_risk_discuss_rounds` is explicitly left alone per the backlog's own framing ("bull/bear round," not risk-discuss round).

**2. Placeholder scan:** No TBDs; the SQL/manual-review steps in Task 2 are literal instructions, not stubs.

**3. Type consistency:** N/A — single `int` field, no new interfaces introduced.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-03-debate-rounds-experiment.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
