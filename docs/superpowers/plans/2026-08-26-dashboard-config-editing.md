# Dashboard Config Editing + Order Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the dashboard edit `candidate_universe.yaml`, `risk_config.yaml`, and 6 named `.env` settings, and cancel open orders — the two capabilities deferred from the process-control build.

**Architecture:** A new `config_editing.py` module owns all file I/O (YAML round-trip, `.env` line patching, audit-log appends) with no FastAPI dependency, so it's independently unit-testable. `dashboard/app.py` gets a `/config` page (3 cards, mirroring `/control`'s layout) plus a Cancel button on `/orders`. Every new write route reuses the existing `_require_same_origin` CSRF guard — no new security mechanism.

**Tech Stack:** FastAPI, Jinja2, `ruamel.yaml` (new dependency, round-trip YAML preserving comments), `apscheduler.triggers.cron.CronTrigger` (cron validation, already a dependency).

**Spec:** docs/superpowers/specs/2026-08-26-dashboard-config-editing-design.md

## Global Constraints

- No process restart required for any edit in this plan — `load_risk_config()`/`load_candidate_universe()`/`Settings()` already re-read their file fresh every cycle (confirmed fact, not a design choice) — never add a restart-signaling mechanism.
- Every new POST route gets `Depends(_require_same_origin)` (`src/tradingsystem/dashboard/app.py`) — no new CSRF mechanism.
- The `.env` file is never fully parsed or loaded into a route or template — only targeted `^{KEY}=` line replacement, every other line (including all secrets) passes through byte-for-byte untouched.
- Both YAML files are edited via `ruamel.yaml`'s round-trip mode, never `yaml.safe_dump` — header comments must survive every edit.
- Never make editable, anywhere in this plan: circuit-breaker clearing, kill switch toggle, any Alpaca/Discord credential, `database_url`, `trading_mode`.
- Order cancellation is one order at a time — no bulk-cancel.
- No eager Alpaca sync after a cancel — the next cycle's `sync_all_open_orders` reconciles status, matching the dashboard's existing fill-latency elsewhere.

---

### Task 1: Config-editing backend module

**Files:**
- Create: `src/tradingsystem/dashboard/config_editing.py`
- Test: `tests/test_config_editing.py`
- Modify: `pyproject.toml` (add `ruamel.yaml>=0.18` to dependencies)

**Interfaces:**
- Consumes: nothing from other tasks (this task has no dependencies).
- Produces (Task 2 calls these exactly):
  - `write_candidate_universe(path: Path, tickers: list[str]) -> None` — raises `ValueError` on invalid input.
  - `read_candidate_universe_tickers(path: Path) -> list[str]`
  - `write_risk_config(path: Path, updates: dict[str, float | int]) -> dict[str, tuple[float | int, float | int]]` — returns `{field: (old, new)}` for only the fields that actually changed. Raises `ValueError` on invalid input.
  - `read_risk_config_values(path: Path) -> dict[str, float | int]`
  - `append_config_change_log(run_dir: Path, changes: dict[str, tuple[float | int, float | int]], note: str) -> None`
  - `read_env_value(path: Path, key: str) -> str | None`
  - `write_env_values(path: Path, updates: dict[str, str]) -> None`

- [ ] **Step 1: Add the dependency**

Add to `pyproject.toml`'s `dependencies` list (matches the existing style, e.g. `"psutil>=5.9"`):
```
"ruamel.yaml>=0.18",
```
Run: `pip install "ruamel.yaml>=0.18"`

- [ ] **Step 2: Write failing tests for candidate-universe editing**

```python
# tests/test_config_editing.py
from pathlib import Path

import pytest

from tradingsystem.dashboard import config_editing


CANDIDATE_UNIVERSE_YAML = """\
# The pool orchestration/ticker_selection.py's discovery screen draws
# candidates from. Freely editable — add/remove tickers here, takes effect
# next cycle.
tickers:
  - AAPL
  - MSFT
"""


def test_read_candidate_universe_tickers(tmp_path):
    path = tmp_path / "candidate_universe.yaml"
    path.write_text(CANDIDATE_UNIVERSE_YAML)

    assert config_editing.read_candidate_universe_tickers(path) == ["AAPL", "MSFT"]


def test_write_candidate_universe_preserves_header_comment(tmp_path):
    path = tmp_path / "candidate_universe.yaml"
    path.write_text(CANDIDATE_UNIVERSE_YAML)

    config_editing.write_candidate_universe(path, ["AAPL", "NVDA", "BRK.B"])

    content = path.read_text()
    assert "Freely editable" in content  # header comment survived the round-trip
    assert config_editing.read_candidate_universe_tickers(path) == ["AAPL", "NVDA", "BRK.B"]


def test_write_candidate_universe_rejects_empty_list(tmp_path):
    path = tmp_path / "candidate_universe.yaml"
    path.write_text(CANDIDATE_UNIVERSE_YAML)

    with pytest.raises(ValueError, match="empty"):
        config_editing.write_candidate_universe(path, [])


def test_write_candidate_universe_rejects_invalid_ticker(tmp_path):
    path = tmp_path / "candidate_universe.yaml"
    path.write_text(CANDIDATE_UNIVERSE_YAML)

    with pytest.raises(ValueError, match="aapl"):
        config_editing.write_candidate_universe(path, ["aapl"])  # lowercase, invalid


def test_write_candidate_universe_accepts_dotted_ticker(tmp_path):
    path = tmp_path / "candidate_universe.yaml"
    path.write_text(CANDIDATE_UNIVERSE_YAML)

    config_editing.write_candidate_universe(path, ["BRK.B"])  # must not raise

    assert config_editing.read_candidate_universe_tickers(path) == ["BRK.B"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_config_editing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradingsystem.dashboard.config_editing'`

- [ ] **Step 4: Implement candidate-universe functions**

```python
# src/tradingsystem/dashboard/config_editing.py
"""File-I/O layer for the dashboard's /config page — dashboard-config-editing-design.md.

No FastAPI dependency here, so every function is testable against a plain
tmp_path without a running app. ruamel.yaml's round-trip mode is used for
both YAML files specifically to preserve their header comments across an
edit — risk_config.yaml's comments state the file's own safety philosophy,
plain yaml.safe_dump would destroy them on the first save.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True

_TICKER_PATTERN = re.compile(r"^[A-Z.]{1,10}$")


def read_candidate_universe_tickers(path: Path) -> list[str]:
    with path.open("r") as f:
        data = _yaml.load(f)
    return list(data["tickers"])


def write_candidate_universe(path: Path, tickers: list[str]) -> None:
    if not tickers:
        raise ValueError("candidate universe must not be empty")
    for ticker in tickers:
        if not _TICKER_PATTERN.match(ticker):
            raise ValueError(f"invalid ticker {ticker!r}: must be uppercase letters/dots only")

    with path.open("r") as f:
        data = _yaml.load(f)
    data["tickers"] = tickers
    with path.open("w") as f:
        _yaml.dump(data, f)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config_editing.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/tradingsystem/dashboard/config_editing.py tests/test_config_editing.py
git commit -m "feat: add candidate-universe editing to config_editing module"
```

- [ ] **Step 7: Write failing tests for risk-config editing**

```python
RISK_CONFIG_YAML = """\
# Risk control parameters, confirmed with the user in ARCHITECTURE.md section 4.
# Changing these is a deliberate decision, not a routine tuning knob.

max_position_pct: 0.10
cash_reserve_pct: 0.20
stop_loss_pct: 0.08

daily_drawdown_breaker_pct: 0.03
weekly_drawdown_breaker_pct: 0.08

stale_data_max_age_minutes: 15
"""


def test_read_risk_config_values(tmp_path):
    path = tmp_path / "risk_config.yaml"
    path.write_text(RISK_CONFIG_YAML)

    values = config_editing.read_risk_config_values(path)

    assert values["max_position_pct"] == 0.10
    assert values["stale_data_max_age_minutes"] == 15


def test_write_risk_config_preserves_header_comment_and_returns_changes(tmp_path):
    path = tmp_path / "risk_config.yaml"
    path.write_text(RISK_CONFIG_YAML)

    changes = config_editing.write_risk_config(path, {
        "max_position_pct": 0.12,
        "cash_reserve_pct": 0.20,  # unchanged — must not appear in the returned dict
    })

    assert changes == {"max_position_pct": (0.10, 0.12)}
    content = path.read_text()
    assert "not a routine tuning knob" in content  # header comment survived
    assert config_editing.read_risk_config_values(path)["max_position_pct"] == 0.12


@pytest.mark.parametrize("field,bad_value", [
    ("max_position_pct", 0.0),
    ("max_position_pct", 1.5),
    ("cash_reserve_pct", -0.1),
    ("stop_loss_pct", 0.0),
    ("daily_drawdown_breaker_pct", 1.1),
    ("weekly_drawdown_breaker_pct", 0.0),
    ("stale_data_max_age_minutes", 0),
    ("stale_data_max_age_minutes", -5),
])
def test_write_risk_config_rejects_out_of_range_values(tmp_path, field, bad_value):
    path = tmp_path / "risk_config.yaml"
    path.write_text(RISK_CONFIG_YAML)

    with pytest.raises(ValueError, match=field):
        config_editing.write_risk_config(path, {field: bad_value})
```

- [ ] **Step 8: Run tests to verify they fail**

Run: `pytest tests/test_config_editing.py -v -k risk_config`
Expected: FAIL with `AttributeError: module ... has no attribute 'read_risk_config_values'`

- [ ] **Step 9: Implement risk-config functions**

Append to `config_editing.py`:

```python
_RISK_CONFIG_PCT_FIELDS = (
    "max_position_pct", "cash_reserve_pct", "stop_loss_pct",
    "daily_drawdown_breaker_pct", "weekly_drawdown_breaker_pct",
)


def read_risk_config_values(path: Path) -> dict[str, float | int]:
    with path.open("r") as f:
        data = _yaml.load(f)
    return {
        **{field: float(data[field]) for field in _RISK_CONFIG_PCT_FIELDS},
        "stale_data_max_age_minutes": int(data["stale_data_max_age_minutes"]),
    }


def _validate_risk_config_field(field: str, value: float | int) -> None:
    if field in _RISK_CONFIG_PCT_FIELDS:
        if not (0 < value <= 1):
            raise ValueError(f"{field} must be in (0, 1], got {value}")
    elif field == "stale_data_max_age_minutes":
        if value <= 0:
            raise ValueError(f"{field} must be > 0, got {value}")
    else:
        raise ValueError(f"unknown risk_config field: {field}")


def write_risk_config(
    path: Path, updates: dict[str, float | int],
) -> dict[str, tuple[float | int, float | int]]:
    for field, value in updates.items():
        _validate_risk_config_field(field, value)

    with path.open("r") as f:
        data = _yaml.load(f)

    changes: dict[str, tuple[float | int, float | int]] = {}
    for field, new_value in updates.items():
        old_value = data[field]
        if field in _RISK_CONFIG_PCT_FIELDS:
            old_value = float(old_value)
        else:
            old_value = int(old_value)
        if old_value != new_value:
            changes[field] = (old_value, new_value)
            data[field] = new_value

    with path.open("w") as f:
        _yaml.dump(data, f)
    return changes
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `pytest tests/test_config_editing.py -v -k risk_config`
Expected: PASS (all 10 parametrized+plain tests)

- [ ] **Step 11: Commit**

```bash
git add src/tradingsystem/dashboard/config_editing.py tests/test_config_editing.py
git commit -m "feat: add risk-config editing to config_editing module"
```

- [ ] **Step 12: Write failing tests for the audit log and .env editing**

```python
def test_append_config_change_log_one_line_per_field(tmp_path):
    changes = {
        "max_position_pct": (0.10, 0.12),
        "stop_loss_pct": (0.08, 0.06),
    }

    config_editing.append_config_change_log(tmp_path, changes, note="tightening risk after a red week")

    log_path = tmp_path / "config_changes.log"
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert "max_position_pct: 0.1 -> 0.12" in lines[0]
    assert 'note="tightening risk after a red week"' in lines[0]
    assert "stop_loss_pct: 0.08 -> 0.06" in lines[1]


def test_append_config_change_log_appends_to_existing_file(tmp_path):
    config_editing.append_config_change_log(tmp_path, {"a": (1, 2)}, note="first")
    config_editing.append_config_change_log(tmp_path, {"b": (3, 4)}, note="second")

    lines = (tmp_path / "config_changes.log").read_text().strip().splitlines()
    assert len(lines) == 2


ENV_FILE = """\
ALPACA_PAPER_API_KEY=super-secret-do-not-touch
DISCOVERY_SLOTS_PER_CYCLE=4
# a comment line
PRE_MARKET_CRON=35 9 * * mon-fri
"""


def test_read_env_value_finds_key(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV_FILE)

    assert config_editing.read_env_value(path, "DISCOVERY_SLOTS_PER_CYCLE") == "4"


def test_read_env_value_missing_key_returns_none(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV_FILE)

    assert config_editing.read_env_value(path, "NOT_PRESENT") is None


def test_write_env_values_replaces_only_matching_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV_FILE)

    config_editing.write_env_values(path, {"DISCOVERY_SLOTS_PER_CYCLE": "6"})

    lines = path.read_text().splitlines()
    assert lines[0] == "ALPACA_PAPER_API_KEY=super-secret-do-not-touch"  # untouched
    assert lines[1] == "DISCOVERY_SLOTS_PER_CYCLE=6"
    assert lines[2] == "# a comment line"  # untouched
    assert lines[3] == "PRE_MARKET_CRON=35 9 * * mon-fri"  # untouched


def test_write_env_values_appends_missing_key(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV_FILE)

    config_editing.write_env_values(path, {"WATCHDOG_CHECK_INTERVAL_MINUTES": "10"})

    assert config_editing.read_env_value(path, "WATCHDOG_CHECK_INTERVAL_MINUTES") == "10"
    assert "ALPACA_PAPER_API_KEY=super-secret-do-not-touch" in path.read_text()  # untouched
```

- [ ] **Step 13: Run tests to verify they fail**

Run: `pytest tests/test_config_editing.py -v -k "config_change_log or env_value or env_values"`
Expected: FAIL with `AttributeError`

- [ ] **Step 14: Implement the audit log and .env functions**

Append to `config_editing.py`:

```python
def append_config_change_log(
    run_dir: Path, changes: dict[str, tuple[float | int, float | int]], note: str,
) -> None:
    run_dir.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f'{timestamp} risk_config {field}: {old} -> {new} | note="{note}"'
        for field, (old, new) in changes.items()
    ]
    with (run_dir / "config_changes.log").open("a") as f:
        for line in lines:
            f.write(line + "\n")


def read_env_value(path: Path, key: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$")
    for line in path.read_text().splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def write_env_values(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text().splitlines()
    remaining = dict(updates)
    new_lines = []
    for line in lines:
        matched_key = None
        for key in remaining:
            if line.startswith(f"{key}="):
                matched_key = key
                break
        if matched_key is not None:
            new_lines.append(f"{matched_key}={remaining.pop(matched_key)}")
        else:
            new_lines.append(line)
    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n")
```

- [ ] **Step 15: Run tests to verify they pass**

Run: `pytest tests/test_config_editing.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 16: Commit**

```bash
git add src/tradingsystem/dashboard/config_editing.py tests/test_config_editing.py
git commit -m "feat: add audit log and .env editing to config_editing module"
```

---

### Task 2: `/config` page and its 3 write routes

**Files:**
- Modify: `src/tradingsystem/dashboard/app.py`
- Create: `src/tradingsystem/dashboard/templates/config.html`
- Modify: `src/tradingsystem/dashboard/templates/base.html:29` (add nav link)
- Test: `tests/test_dashboard_config.py`

**Interfaces:**
- Consumes (from Task 1, exact signatures): `config_editing.read_candidate_universe_tickers(path) -> list[str]`, `config_editing.write_candidate_universe(path, tickers) -> None` (raises `ValueError`), `config_editing.read_risk_config_values(path) -> dict`, `config_editing.write_risk_config(path, updates) -> dict[str, tuple]` (raises `ValueError`), `config_editing.append_config_change_log(run_dir, changes, note) -> None`, `config_editing.read_env_value(path, key) -> str | None`, `config_editing.write_env_values(path, updates) -> None`.
- Consumes (existing): `process_control.RUN_DIR` (the `run/` directory), `_require_same_origin` (`dashboard/app.py`), `apscheduler.triggers.cron.CronTrigger.from_crontab`.
- Produces: nothing further tasks depend on (Task 3 is independent).

The config file paths are module-level constants in `app.py` so tests can `monkeypatch.setattr` them, exactly like `test_process_control.py` monkeypatches `process_control.RUN_DIR`:

```python
_RISK_CONFIG_PATH = REPO_ROOT / "config" / "risk_config.yaml"
_CANDIDATE_UNIVERSE_PATH = REPO_ROOT / "config" / "candidate_universe.yaml"
_ENV_PATH = REPO_ROOT / ".env"
```

`REPO_ROOT` comes from `tradingsystem.config` (already used elsewhere, e.g. `process_control.py`'s `RUN_DIR = REPO_ROOT / "run"`).

- [ ] **Step 1: Write failing tests for GET /config**

```python
# tests/test_dashboard_config.py
from tradingsystem.dashboard import app as app_module
from tradingsystem.dashboard import config_editing


CANDIDATE_UNIVERSE_YAML = "tickers:\n  - AAPL\n  - MSFT\n"
RISK_CONFIG_YAML = (
    "max_position_pct: 0.10\ncash_reserve_pct: 0.20\nstop_loss_pct: 0.08\n"
    "daily_drawdown_breaker_pct: 0.03\nweekly_drawdown_breaker_pct: 0.08\n"
    "stale_data_max_age_minutes: 15\n"
)
ENV_FILE = "DISCOVERY_SLOTS_PER_CYCLE=4\nPRE_MARKET_CRON=35 9 * * mon-fri\n"


def _write_config_fixtures(tmp_path, monkeypatch):
    candidate_path = tmp_path / "candidate_universe.yaml"
    candidate_path.write_text(CANDIDATE_UNIVERSE_YAML)
    risk_path = tmp_path / "risk_config.yaml"
    risk_path.write_text(RISK_CONFIG_YAML)
    env_path = tmp_path / ".env"
    env_path.write_text(ENV_FILE)
    monkeypatch.setattr(app_module, "_CANDIDATE_UNIVERSE_PATH", candidate_path)
    monkeypatch.setattr(app_module, "_RISK_CONFIG_PATH", risk_path)
    monkeypatch.setattr(app_module, "_ENV_PATH", env_path)
    return candidate_path, risk_path, env_path


def test_config_page_shows_current_values(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)

    response = client.get("/config")

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "0.1" in response.text  # max_position_pct rendered somewhere
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_config.py -v`
Expected: FAIL with 404 (no `/config` route yet)

- [ ] **Step 3: Implement GET /config**

Add to `dashboard/app.py` (near the top, alongside the other imports and `TEMPLATES_DIR`):

```python
from apscheduler.triggers.cron import CronTrigger

from tradingsystem.config import REPO_ROOT
from tradingsystem.dashboard import config_editing

_RISK_CONFIG_PATH = REPO_ROOT / "config" / "risk_config.yaml"
_CANDIDATE_UNIVERSE_PATH = REPO_ROOT / "config" / "candidate_universe.yaml"
_ENV_PATH = REPO_ROOT / ".env"
_ENV_FIELDS = (
    "discovery_slots_per_cycle", "watchdog_check_interval_minutes",
    "pre_market_cron", "midday_cron",
    "tradingagents_deep_think_model", "tradingagents_quick_think_model",
)
```

Add the route (anywhere among the other `@app.get` routes):

```python
@app.get("/config")
def config_page(request: Request):
    env_values = {
        field: config_editing.read_env_value(_ENV_PATH, field.upper())
        for field in _ENV_FIELDS
    }
    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "tickers": config_editing.read_candidate_universe_tickers(_CANDIDATE_UNIVERSE_PATH),
            "risk_config": config_editing.read_risk_config_values(_RISK_CONFIG_PATH),
            "env_values": env_values,
        },
    )
```

Create `dashboard/templates/config.html`:

```html
{% extends "base.html" %}
{% block title %}Config — Bot-Trading Dashboard{% endblock %}
{% block content %}
<h1>Config</h1>

<div class="card">
    <h2>Candidate Universe</h2>
    <p>Freely editable — add/remove tickers, takes effect next cycle.</p>
    <form method="post" action="/config/candidate-universe">
        <textarea name="tickers" rows="10" cols="20">{{ tickers|join("\n") }}</textarea><br>
        <button type="submit">Save</button>
    </form>
</div>

<div class="card">
    <h2>Risk Config</h2>
    <p>Changing these is a deliberate decision — a justification is required.</p>
    <form method="post" action="/config/risk-config">
        <label>Max position % <input type="number" step="0.01" name="max_position_pct" value="{{ risk_config.max_position_pct }}"></label><br>
        <label>Cash reserve % <input type="number" step="0.01" name="cash_reserve_pct" value="{{ risk_config.cash_reserve_pct }}"></label><br>
        <label>Stop-loss % <input type="number" step="0.01" name="stop_loss_pct" value="{{ risk_config.stop_loss_pct }}"></label><br>
        <label>Daily breaker % <input type="number" step="0.01" name="daily_drawdown_breaker_pct" value="{{ risk_config.daily_drawdown_breaker_pct }}"></label><br>
        <label>Weekly breaker % <input type="number" step="0.01" name="weekly_drawdown_breaker_pct" value="{{ risk_config.weekly_drawdown_breaker_pct }}"></label><br>
        <label>Stale data max age (min) <input type="number" name="stale_data_max_age_minutes" value="{{ risk_config.stale_data_max_age_minutes }}"></label><br>
        <label>Justification (required)<br><textarea name="note" rows="3" cols="40" required></textarea></label><br>
        <button type="submit">Save</button>
    </form>
</div>

<div class="card">
    <h2>Env Settings</h2>
    <form method="post" action="/config/env-settings">
        <label>Discovery slots per cycle <input type="number" name="discovery_slots_per_cycle" value="{{ env_values.discovery_slots_per_cycle }}"></label><br>
        <label>Watchdog check interval (min) <input type="number" name="watchdog_check_interval_minutes" value="{{ env_values.watchdog_check_interval_minutes }}"></label><br>
        <label>Pre-market cron <input type="text" name="pre_market_cron" value="{{ env_values.pre_market_cron }}"></label><br>
        <label>Midday cron <input type="text" name="midday_cron" value="{{ env_values.midday_cron }}"></label><br>
        <label>Deep-think model <input type="text" name="tradingagents_deep_think_model" value="{{ env_values.tradingagents_deep_think_model }}"></label><br>
        <label>Quick-think model <input type="text" name="tradingagents_quick_think_model" value="{{ env_values.tradingagents_quick_think_model }}"></label><br>
        <button type="submit">Save</button>
    </form>
</div>
{% endblock %}
```

In `base.html`, change line 29 from:
```html
        <a href="/control">Control</a>
```
to:
```html
        <a href="/control">Control</a>
        <a href="/config">Config</a>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tradingsystem/dashboard/app.py src/tradingsystem/dashboard/templates/config.html src/tradingsystem/dashboard/templates/base.html tests/test_dashboard_config.py
git commit -m "feat: add GET /config page"
```

- [ ] **Step 6: Write failing tests for the 3 POST routes**

```python
def test_post_candidate_universe_saves_and_redirects(client, tmp_path, monkeypatch):
    candidate_path, _, _ = _write_config_fixtures(tmp_path, monkeypatch)

    response = client.post(
        "/config/candidate-universe", data={"tickers": "AAPL\nNVDA"}, follow_redirects=False,
    )

    assert response.status_code == 303
    assert config_editing.read_candidate_universe_tickers(candidate_path) == ["AAPL", "NVDA"]


def test_post_candidate_universe_rejects_invalid_ticker(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)

    response = client.post("/config/candidate-universe", data={"tickers": "aapl"})

    assert response.status_code == 422


def test_post_candidate_universe_rejects_mismatched_origin(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)

    response = client.post(
        "/config/candidate-universe", data={"tickers": "AAPL"},
        headers={"origin": "http://evil.example"},
    )

    assert response.status_code == 403


def test_post_risk_config_saves_and_logs(client, tmp_path, monkeypatch):
    _, risk_path, _ = _write_config_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module.process_control, "RUN_DIR", tmp_path / "run")

    response = client.post(
        "/config/risk-config",
        data={
            "max_position_pct": "0.12", "cash_reserve_pct": "0.20", "stop_loss_pct": "0.08",
            "daily_drawdown_breaker_pct": "0.03", "weekly_drawdown_breaker_pct": "0.08",
            "stale_data_max_age_minutes": "15", "note": "raising the cap after a good run",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert config_editing.read_risk_config_values(risk_path)["max_position_pct"] == 0.12
    log_content = (tmp_path / "run" / "config_changes.log").read_text()
    assert "max_position_pct: 0.1 -> 0.12" in log_content
    assert 'note="raising the cap after a good run"' in log_content


def test_post_risk_config_rejects_empty_justification(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module.process_control, "RUN_DIR", tmp_path / "run")

    response = client.post(
        "/config/risk-config",
        data={
            "max_position_pct": "0.12", "cash_reserve_pct": "0.20", "stop_loss_pct": "0.08",
            "daily_drawdown_breaker_pct": "0.03", "weekly_drawdown_breaker_pct": "0.08",
            "stale_data_max_age_minutes": "15", "note": "   ",
        },
    )

    assert response.status_code == 422


def test_post_risk_config_rejects_out_of_range_value(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module.process_control, "RUN_DIR", tmp_path / "run")

    response = client.post(
        "/config/risk-config",
        data={
            "max_position_pct": "1.5", "cash_reserve_pct": "0.20", "stop_loss_pct": "0.08",
            "daily_drawdown_breaker_pct": "0.03", "weekly_drawdown_breaker_pct": "0.08",
            "stale_data_max_age_minutes": "15", "note": "testing an out of range value",
        },
    )

    assert response.status_code == 422


def test_post_env_settings_saves(client, tmp_path, monkeypatch):
    _, _, env_path = _write_config_fixtures(tmp_path, monkeypatch)

    response = client.post(
        "/config/env-settings",
        data={
            "discovery_slots_per_cycle": "6", "watchdog_check_interval_minutes": "20",
            "pre_market_cron": "35 9 * * mon-fri", "midday_cron": "30 12 * * mon-fri",
            "tradingagents_deep_think_model": "gpt-oss:120b-cloud",
            "tradingagents_quick_think_model": "nemotron-3-super:cloud",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert config_editing.read_env_value(env_path, "DISCOVERY_SLOTS_PER_CYCLE") == "6"


def test_post_env_settings_rejects_invalid_cron(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)

    response = client.post(
        "/config/env-settings",
        data={
            "discovery_slots_per_cycle": "6", "watchdog_check_interval_minutes": "20",
            "pre_market_cron": "not a cron expression", "midday_cron": "30 12 * * mon-fri",
            "tradingagents_deep_think_model": "gpt-oss:120b-cloud",
            "tradingagents_quick_think_model": "nemotron-3-super:cloud",
        },
    )

    assert response.status_code == 422
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_config.py -v`
Expected: FAIL with 404 on all 3 POST routes

- [ ] **Step 8: Implement the 3 POST routes**

Add to `dashboard/app.py`:

```python
@app.post("/config/candidate-universe")
def post_candidate_universe(tickers: str = Form(...), _: None = Depends(_require_same_origin)):
    ticker_list = [line.strip() for line in tickers.splitlines() if line.strip()]
    try:
        config_editing.write_candidate_universe(_CANDIDATE_UNIVERSE_PATH, ticker_list)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse("/config", status_code=303)


@app.post("/config/risk-config")
def post_risk_config(
    max_position_pct: float = Form(...),
    cash_reserve_pct: float = Form(...),
    stop_loss_pct: float = Form(...),
    daily_drawdown_breaker_pct: float = Form(...),
    weekly_drawdown_breaker_pct: float = Form(...),
    stale_data_max_age_minutes: int = Form(...),
    note: str = Form(...),
    _: None = Depends(_require_same_origin),
):
    note = note.strip()
    if not note:
        raise HTTPException(status_code=422, detail="justification is required")
    if len(note) > 2000:
        raise HTTPException(status_code=422, detail="justification must be 2000 characters or fewer")

    updates = {
        "max_position_pct": max_position_pct,
        "cash_reserve_pct": cash_reserve_pct,
        "stop_loss_pct": stop_loss_pct,
        "daily_drawdown_breaker_pct": daily_drawdown_breaker_pct,
        "weekly_drawdown_breaker_pct": weekly_drawdown_breaker_pct,
        "stale_data_max_age_minutes": stale_data_max_age_minutes,
    }
    try:
        changes = config_editing.write_risk_config(_RISK_CONFIG_PATH, updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if changes:
        config_editing.append_config_change_log(process_control.RUN_DIR, changes, note)
    return RedirectResponse("/config", status_code=303)


@app.post("/config/env-settings")
def post_env_settings(
    discovery_slots_per_cycle: int = Form(...),
    watchdog_check_interval_minutes: int = Form(...),
    pre_market_cron: str = Form(...),
    midday_cron: str = Form(...),
    tradingagents_deep_think_model: str = Form(...),
    tradingagents_quick_think_model: str = Form(...),
    _: None = Depends(_require_same_origin),
):
    if discovery_slots_per_cycle < 0:
        raise HTTPException(status_code=422, detail="discovery_slots_per_cycle must be >= 0")
    if watchdog_check_interval_minutes <= 0:
        raise HTTPException(status_code=422, detail="watchdog_check_interval_minutes must be > 0")
    for cron_value, field in ((pre_market_cron, "pre_market_cron"), (midday_cron, "midday_cron")):
        try:
            CronTrigger.from_crontab(cron_value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid {field}: {exc}") from exc
    if not tradingagents_deep_think_model.strip() or not tradingagents_quick_think_model.strip():
        raise HTTPException(status_code=422, detail="model names must not be empty")

    config_editing.write_env_values(_ENV_PATH, {
        "DISCOVERY_SLOTS_PER_CYCLE": str(discovery_slots_per_cycle),
        "WATCHDOG_CHECK_INTERVAL_MINUTES": str(watchdog_check_interval_minutes),
        "PRE_MARKET_CRON": pre_market_cron,
        "MIDDAY_CRON": midday_cron,
        "TRADINGAGENTS_DEEP_THINK_MODEL": tradingagents_deep_think_model,
        "TRADINGAGENTS_QUICK_THINK_MODEL": tradingagents_quick_think_model,
    })
    return RedirectResponse("/config", status_code=303)
```

Add `Form` to the existing `from fastapi import ...` line, and `RedirectResponse` to the existing `from fastapi.responses import ...` line (create that import line if it doesn't exist yet — check the current imports first, this app has no redirect today).

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_config.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 10: Run the full suite**

Run: `pytest -q`
Expected: PASS, no regressions in existing dashboard/config tests

- [ ] **Step 11: Commit**

```bash
git add src/tradingsystem/dashboard/app.py tests/test_dashboard_config.py
git commit -m "feat: add candidate-universe, risk-config, and env-settings write routes"
```

---

### Task 3: Order cancellation

**Files:**
- Modify: `src/tradingsystem/dashboard/app.py`
- Modify: `src/tradingsystem/dashboard/templates/orders.html`
- Test: `tests/test_dashboard_orders_cancel.py`

**Interfaces:**
- Consumes: `executor._TERMINAL_ORDER_STATUSES` (`src/tradingsystem/execution/executor.py:107`, the existing `{"filled", "canceled", "expired", "rejected"}` set — import it, do not redefine it). `AlpacaClient.cancel_order(alpaca_order_id: str) -> None` (`alpaca_client.py:199-201`, already exists, raises on failure — the underlying SDK's exception type, not caught/wrapped by `AlpacaClient` itself).
- Produces: nothing further tasks depend on.

This task adds a new FastAPI dependency, `get_alpaca_client`, using the same `app.dependency_overrides` mechanism this codebase already uses for `get_db` (see `tests/conftest.py:54`, `app.dependency_overrides[get_db] = lambda: db_session` in the `client` fixture) — confirmed as the established, working pattern in this codebase. A plain default-argument reference (e.g. `monkeypatch.setattr(app_module, "get_alpaca_client", ...)`) would NOT work here, because FastAPI captures the function object at route-definition time, not by name lookup per-request.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_orders_cancel.py
import datetime
import uuid

from tradingsystem.dashboard.app import app, get_alpaca_client
from tradingsystem.db.models import AgentRun, Decision, Order


class FakeCancelAlpacaClient:
    def __init__(self, raise_on_cancel=False):
        self.cancel_calls = []
        self.raise_on_cancel = raise_on_cancel

    def cancel_order(self, alpaca_order_id):
        self.cancel_calls.append(alpaca_order_id)
        if self.raise_on_cancel:
            raise RuntimeError("simulated Alpaca rejection: order already filled")


def _make_order(db_session, status="new"):
    now = datetime.datetime.utcnow()
    run = AgentRun(ticker="AAPL", run_type="pre_market", started_at=now, finished_at=now,
                    market_status="open", outcome="decision_recorded")
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="test")
    db_session.add(decision)
    db_session.flush()
    order = Order(decision_id=decision.id, ticker="AAPL", side="buy", qty=10, limit_price=100.0,
                  status=status, alpaca_order_id="abc123", submitted_at=now)
    db_session.add(order)
    db_session.flush()
    return order


def test_cancel_open_order_succeeds(client, db_session):
    order = _make_order(db_session, status="new")
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/orders/{order.id}/cancel", follow_redirects=False)

    assert response.status_code == 303
    assert fake_client.cancel_calls == ["abc123"]


def test_cancel_terminal_order_returns_409_without_calling_alpaca(client, db_session):
    order = _make_order(db_session, status="filled")
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/orders/{order.id}/cancel")

    assert response.status_code == 409
    assert fake_client.cancel_calls == []


def test_cancel_missing_order_returns_404(client, db_session):
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/orders/{uuid.uuid4()}/cancel")

    assert response.status_code == 404


def test_cancel_surfaces_alpaca_rejection_as_409(client, db_session):
    order = _make_order(db_session, status="new")
    fake_client = FakeCancelAlpacaClient(raise_on_cancel=True)
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/orders/{order.id}/cancel")

    assert response.status_code == 409
    assert "already filled" in response.json()["detail"]


def test_cancel_rejects_mismatched_origin(client, db_session):
    order = _make_order(db_session, status="new")
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/orders/{order.id}/cancel", headers={"origin": "http://evil.example"})

    assert response.status_code == 403
    assert fake_client.cancel_calls == []


def test_orders_page_shows_cancel_button_only_for_open_orders(client, db_session):
    _make_order(db_session, status="new")

    response = client.get("/orders")

    assert response.status_code == 200
    assert "/cancel" in response.text
```

Note: `app.dependency_overrides` is cleared by the existing `client` fixture's teardown (`tests/conftest.py:56`), so no manual cleanup is needed in these tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_orders_cancel.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_alpaca_client'`

- [ ] **Step 3: Implement the dependency and route**

Add to `dashboard/app.py`'s imports:
```python
from tradingsystem.config import Settings
from tradingsystem.execution.alpaca_client import AlpacaClient, AlpacaClientProtocol
from tradingsystem.execution.executor import _TERMINAL_ORDER_STATUSES
```

Add near `get_db`:
```python
def get_alpaca_client() -> AlpacaClientProtocol:
    return AlpacaClient(Settings())
```

Add the route (near the existing `/orders` GET route):
```python
@app.post("/orders/{order_id}/cancel")
def cancel_order_route(
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    alpaca_client: AlpacaClientProtocol = Depends(get_alpaca_client),
    _: None = Depends(_require_same_origin),
):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status in _TERMINAL_ORDER_STATUSES:
        raise HTTPException(status_code=409, detail=f"order already {order.status}, nothing to cancel")
    try:
        alpaca_client.cancel_order(order.alpaca_order_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse("/orders", status_code=303)
```

In `dashboard/templates/orders.html`, change the header row (line 8) from:
```html
        <tr><th>Submitted</th><th>Ticker</th><th>Side</th><th>Qty</th><th>Limit</th><th>Status</th><th>Decision</th><th>Fills</th></tr>
```
to:
```html
        <tr><th>Submitted</th><th>Ticker</th><th>Side</th><th>Qty</th><th>Limit</th><th>Status</th><th>Decision</th><th>Fills</th><th>Cancel</th></tr>
```

And after the `<td>` block ending at line 30 (the Fills `</td>`), add a new `<td>` before the closing `</tr>`:
```html
            <td>
                {% if order.status not in terminal_order_statuses %}
                <form method="post" action="/orders/{{ order.id }}/cancel" style="display:inline">
                    <button type="submit">Cancel</button>
                </form>
                {% else %}
                -
                {% endif %}
            </td>
```

Update the `{% else %}` row's `colspan` (line 33) from `colspan="8"` to `colspan="9"`.

Update the `orders_list` route (`dashboard/app.py`) to pass the terminal-status set to the template:
```python
@app.get("/orders")
def orders_list(request: Request, db: Session = Depends(get_db)):
    orders = db.query(Order).order_by(Order.submitted_at.desc()).all()
    return templates.TemplateResponse(
        request, "orders.html", {"orders": orders, "terminal_order_statuses": _TERMINAL_ORDER_STATUSES},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_orders_cancel.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS, no regressions (including the existing `tests/test_dashboard_orders.py`, if present, which must still pass with the new `terminal_order_statuses` context key added)

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/dashboard/app.py src/tradingsystem/dashboard/templates/orders.html tests/test_dashboard_orders_cancel.py
git commit -m "feat: add order cancellation to the dashboard"
```
