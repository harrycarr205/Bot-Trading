# Dashboard Config Editing + Order Cancellation — Design Spec

**Status:** Approved by user in chat 2026-08-26, written up here for the record.

## Purpose

The `dashboard-process-control` branch (merged 2026-08-26) added start/stop
control for the scheduler/watchdog and an off-cycle trigger, but explicitly
deferred two capabilities the user scoped in during that build's "walk
through the controls" conversation:

1. **Config editing** — `candidate_universe.yaml`, `risk_config.yaml`, and a
   fixed set of `.env` settings.
2. **Order cancellation** — exposing `AlpacaClient.cancel_order`, which today
   exists in code but has zero UI or CLI exposure ("manual utility only —
   never called automatically by executor.py").

Circuit-breaker clearing and the kill switch remain explicitly out of scope
for the dashboard, by the user's own repeated decision across both scoping
rounds — CLI-only, on purpose, so a clear always requires a human-typed name
and note (ARCHITECTURE.md §7).

## Why this needs a spec, not a bounded change

`risk_config.yaml` is described in its own file header as something "the
validation layer treats as gospel" and changing it as "a deliberate
decision, not a routine tuning knob" — editing it from a dashboard reopens
that safety framing the same way process-control reopened the
breaker-clearing friction question. Order cancellation is a new write
capability over live (paper) broker state. Both need the same weight of
design as the process-control build got.

## Confirmed, not re-decided here

These were settled in the prior scoping conversation and are not reopened:
- Dashboard-editable: `candidate_universe.yaml`, `risk_config.yaml`, select
  `.env` settings, and order cancellation only.
- Never dashboard-editable: circuit-breaker clearing, kill switch toggle,
  any Alpaca/Discord API key, `database_url`, `trading_mode`.

## Confirmed facts about the current code

- `load_risk_config()` and `load_candidate_universe()`
  (`src/tradingsystem/config.py:99-108`) both read their YAML file from disk
  fresh on every call. `run_full_cycle` calls them (or `Settings()`, for
  `.env`-backed fields) once per cycle with no caching across cycles. **A
  config edit therefore takes effect on the next cycle automatically — no
  process restart required** — for every field this spec covers. This
  matches `candidate_universe.yaml`'s own header comment ("takes effect next
  cycle. No code change required.").
- `_TERMINAL_ORDER_STATUSES = {"filled", "canceled", "expired", "rejected"}`
  already exists at `src/tradingsystem/execution/executor.py:107` and gates
  `sync_all_open_orders`'s open-order query. The Cancel button reuses this
  exact constant rather than inventing a parallel one.
- `AlpacaClient.cancel_order(alpaca_order_id)` already exists
  (`alpaca_client.py:199-201`) and calls `self._client.cancel_order_by_id`.
  Alpaca's own API is the authoritative gate on whether a given order is
  actually cancellable at the moment of the call — our local `status` check
  is a UI convenience (don't show the button when it can't work), not the
  safety boundary. A cancel that Alpaca itself rejects (order transitioned
  to filled between page load and click) must surface as a clear error, not
  a crash.
- `_require_same_origin` (CSRF guard, `dashboard/app.py`) and the
  `run/`-directory convention for operational logs are both established
  patterns from the process-control build; this build reuses them rather
  than inventing new ones.

## Design

### 1. New page: `/config`

Mirrors `/control`'s existing card layout (same base template, same
dark-terminal styling). Three cards, in this order: Candidate Universe,
Risk Config, Env Settings. Nav link added to `base.html` next to the
existing `/control` link.

### 2. Candidate Universe card

A `<textarea>` pre-filled with the current tickers (one per line, current
comments in the YAML are NOT round-tripped through this — see the YAML
round-trip decision below) plus a Save button.

**Validation before write:** the submitted list must be non-empty, and each
line, after stripping whitespace, must match `^[A-Z.]{1,10}$` (uppercase
letters and dots only, covering tickers like `BRK.B`; rejects blank lines,
lowercase, punctuation typos). Reject the whole submission with a 422 and an
error message on any invalid line — don't silently drop bad lines.

**No justification required** — matches the file's own existing "freely
editable" framing, same friction level as today's manual edit.

### 3. Risk Config card

One numeric input per `RiskConfig` field, pre-filled with current values,
plus a **required** justification `<textarea>`.

| Field | Validation |
|---|---|
| `max_position_pct` | `0 < x <= 1` |
| `cash_reserve_pct` | `0 < x <= 1` |
| `stop_loss_pct` | `0 < x <= 1` |
| `daily_drawdown_breaker_pct` | `0 < x <= 1` |
| `weekly_drawdown_breaker_pct` | `0 < x <= 1` |
| `stale_data_max_age_minutes` | integer `> 0` |

Justification: non-empty after stripping whitespace, max 2000 chars.

**On successful save:** write the new values to `risk_config.yaml`, and
append one line to `run/config_changes.log` (created under the same
gitignored `run/` directory the process-control build already established
for operational logs — no new top-level directory, no DB migration):

```
2026-08-26T14:32:07Z risk_config max_position_pct: 0.10 -> 0.12 | note="raising position cap after two weeks of clean paper performance, want more concentration on high-conviction picks"
```

One line per *changed* field (not one line per submission) — if a submit
changes three fields, that's three log lines sharing the same timestamp and
note, so the log stays greppable per-field. Unchanged fields in the
submission are not logged.

### 4. Env Settings card

Six fields, matching the user's selection exactly:

| Field | Validation |
|---|---|
| `discovery_slots_per_cycle` | integer `>= 0` |
| `watchdog_check_interval_minutes` | integer `> 0` |
| `pre_market_cron` | must parse via `apscheduler.triggers.cron.CronTrigger.from_crontab` without raising |
| `midday_cron` | same |
| `tradingagents_deep_think_model` | non-empty string, no further validation (we don't enumerate valid Ollama Cloud model names) |
| `tradingagents_quick_think_model` | same |

**Write mechanism:** the `.env` file is never fully parsed or loaded into
the route or template. A small helper reads the file as lines, replaces
only the line(s) matching `^{KEY}=` for the fields actually submitted, and
writes the file back — every other line (including all secrets) passes
through untouched, byte-for-byte. If a key isn't present in the file yet
(e.g. a fresh `.env` copied from `.env.example` before this field was ever
set), append a new `KEY=value` line rather than failing.

No justification required for this card — these are operational tuning
knobs, not the "gospel" safety parameters risk_config represents.

### 5. Order cancellation

Add a "Cancel" column to the existing `/orders` page's table. Button is
rendered (not just disabled) only when
`order.status not in _TERMINAL_ORDER_STATUSES` (imported from
`executor.py`, not redefined). Clicking POSTs to
`/orders/{order_id}/cancel`, which:

1. Looks up the `Order` row by ID (404 if missing).
2. If `order.status in _TERMINAL_ORDER_STATUSES`, return 409 — already
   terminal, nothing to do (handles the race where status changed since
   page load).
3. Calls `alpaca_client.cancel_order(order.alpaca_order_id)`. If Alpaca
   itself rejects the cancel (already filled/etc. on their side), catch the
   exception and return a 409 with Alpaca's error message rather than a
   500.
4. On success, redirect back to `/orders` — the row's status will reflect
   as `canceled` next time `sync_all_open_orders` runs (next cycle, or the
   next dashboard load if we choose to sync eagerly — decision below).

**Open question resolved:** don't add an eager sync-on-cancel call. Keep
this consistent with how the rest of the dashboard already treats order
status (`sync_all_open_orders` runs once per cycle, not on-demand) — a
canceled order shows its pre-cancel status until the next cycle syncs it,
same latency characteristic the `/orders` page already has for fills.

### 6. Security

All four new write routes (candidate-universe save, risk-config save,
env-settings save, order cancel) get `Depends(_require_same_origin)`,
identical to every write route the process-control build added — no new
CSRF mechanism.

### 7. YAML round-trip decision

`candidate_universe.yaml` and `risk_config.yaml` both carry meaningful
header comments (the "gospel" framing on the latter especially). Two
options:

- **A — plain `yaml.safe_dump` on write.** Simple, no new dependency, but
  destroys all comments in the file on the first dashboard-driven edit.
- **B — `ruamel.yaml` round-trip mode.** Preserves comments and key order
  on write. New dependency (`ruamel.yaml`), slightly more code to load/dump
  correctly.

**Recommendation: B.** The comments in `risk_config.yaml` aren't
decorative — they're the file's stated philosophy ("not a routine tuning
knob") and the only place `stale_data_max_age_minutes` and the breaker
percentages are explained. Losing them on the very first edit undermines
the reason this card requires a justification in the first place. The
implementation plan should add `ruamel.yaml` to `pyproject.toml` and use
its round-trip loader/dumper for both YAML files.

## Testing

Standard patterns already established in this codebase apply throughout:
`TestClient` + `monkeypatch` for route tests (matching
`test_dashboard_control.py`'s style), `tmp_path`-based file redirection for
anything touching real files (matching `test_process_control.py`'s
`monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)` pattern — this
build will need an equivalent for the config file paths and `.env` path),
and the existing `FakeAlpacaClient` (`test_cycle.py`) extended with a
`cancel_order` call-recording stub for the order-cancellation tests.

## Out of scope (explicit)

- Circuit-breaker clearing, kill switch toggle — CLI-only, permanently, by
  standing user decision.
- Any Alpaca/Discord credential, `database_url`, `trading_mode` — never
  dashboard-editable.
- Editing `.env` fields beyond the six listed in §4.
- Bulk/partial order cancellation (cancel-all button) — one order at a time
  only, matching the existing `/orders` page's per-row action model.
- Undo for a risk-config or env-settings save — `run/config_changes.log` is
  an audit trail, not an undo mechanism; reverting means editing the form
  again with the old value.
