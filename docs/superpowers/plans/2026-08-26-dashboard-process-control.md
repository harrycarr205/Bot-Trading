# Dashboard Process Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the dashboard from a passive viewer into a real control surface for the scheduler/watchdog processes — start/stop, genuine live status, an off-cycle run trigger, and log viewing — via PID-lockfiles, without touching circuit-breaker clearing or the kill switch.

**Architecture:** A new shared `orchestration/process_control.py` module owns PID-lockfile read/write and stop-request signaling. `scheduler.py`/`watchdog.py` write their own lockfile on startup and poll for a stop request via a frequent APScheduler interval job; `cycle.py`'s per-ticker loop also checks for a stop request so it never aborts a ticker mid-flight. A new `run_once.py` entry point handles off-cycle runs independently of the persistent scheduler. The dashboard gets its first-ever write routes, calling into `process_control` to start/stop/status-check both processes.

**Tech Stack:** Python 3.12, APScheduler (already a dependency), `psutil` (new dependency, for reliable cross-process PID-liveness checks and termination), FastAPI/Jinja2 (existing dashboard stack).

**Spec:** `docs/superpowers/specs/2026-08-26-dashboard-process-control-design.md`

## Global Constraints

- Stop is graceful-with-a-60-second-timeout: never aborts a ticker already in progress mid-cycle, only declines to start the *next* one. If the process hasn't exited cleanly within 60s, force-kill it and report that a forced kill was needed.
- Starting an already-alive process must be refused (return an error), never spawn a second competing instance.
- Windows-only: `subprocess.DETACHED_PROCESS`/`subprocess.CREATE_NEW_PROCESS_GROUP` are real Windows-only constants — no cross-platform branching needed anywhere in this plan.
- Circuit-breaker clearing and the kill switch are explicitly out of scope — nothing in this plan touches `db/clear_breaker.py` or `risk/kill_switch.py`.
- Config editing (`candidate_universe.yaml`, `risk_config.yaml`, `.env` settings) and order cancellation are explicitly deferred to a second, separate build — not part of this plan.
- Tests run via `.venv/Scripts/python.exe -m pytest`, against the dedicated `trading_test` database (the `db_session`/`client` fixtures in `tests/conftest.py`), never the live `trading` database.

---

### Task 1: `process_control.py` — PID-lockfiles and stop-requests

**Files:**
- Create: `src/tradingsystem/orchestration/process_control.py`
- Test: `tests/test_process_control.py`
- Modify: `pyproject.toml` (add `psutil` dependency)
- Modify: `.gitignore` (add `run/`)

**Interfaces:**
- Produces: `RUN_DIR: Path` (module-level constant, `REPO_ROOT / "run"`), `ProcessStatus` (frozen dataclass: `name: str`, `pid: int | None`, `alive: bool`), `write_pidfile(name: str) -> None`, `remove_pidfile(name: str) -> None`, `get_process_status(name: str) -> ProcessStatus`, `request_stop(name: str) -> None`, `is_stop_requested(name: str) -> bool`, `clear_stop_request(name: str) -> None`, `spawn_detached(module: str) -> int`, `force_kill(pid: int) -> None`.

- [ ] **Step 1: Add `psutil` as a dependency and install it**

In `pyproject.toml`, replace:

```toml
    "python-dotenv>=1.0",
    "tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git@v0.3.1",
```

with:

```toml
    "python-dotenv>=1.0",
    "psutil>=5.9",
    "tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git@v0.3.1",
```

Then install it into the venv:

Run: `.venv/Scripts/python.exe -m pip install psutil>=5.9`
Expected: installs successfully. Verify with `.venv/Scripts/python.exe -c "import psutil; print(psutil.__version__)"` — should print a version number, not an error.

- [ ] **Step 2: Add `run/` to `.gitignore`**

In `.gitignore`, replace:

```
logs/
*.log
```

with:

```
logs/
*.log
run/
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_process_control.py`:

```python
import psutil

from tradingsystem.orchestration import process_control


def test_write_pidfile_then_get_process_status_reports_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)

    process_control.write_pidfile("scheduler")

    status = process_control.get_process_status("scheduler")
    assert status.name == "scheduler"
    assert status.pid == psutil.Process().pid  # this test process's own PID
    assert status.alive is True


def test_get_process_status_no_pidfile_reports_not_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)

    status = process_control.get_process_status("scheduler")

    assert status.pid is None
    assert status.alive is False


def test_get_process_status_stale_pidfile_reports_not_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)
    # A PID very unlikely to correspond to a real running process.
    (tmp_path / "scheduler.pid").write_text("999999")

    status = process_control.get_process_status("scheduler")

    assert status.pid == 999999
    assert status.alive is False


def test_remove_pidfile_clears_status(tmp_path, monkeypatch):
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)
    process_control.write_pidfile("scheduler")

    process_control.remove_pidfile("scheduler")

    assert process_control.get_process_status("scheduler").alive is False


def test_remove_pidfile_missing_file_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)

    process_control.remove_pidfile("scheduler")  # no pidfile ever written


def test_stop_request_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)

    assert process_control.is_stop_requested("scheduler") is False

    process_control.request_stop("scheduler")
    assert process_control.is_stop_requested("scheduler") is True

    process_control.clear_stop_request("scheduler")
    assert process_control.is_stop_requested("scheduler") is False


def test_clear_stop_request_missing_file_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)

    process_control.clear_stop_request("scheduler")  # never requested
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_process_control.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingsystem.orchestration.process_control'`

- [ ] **Step 5: Implement `process_control.py`**

Create `src/tradingsystem/orchestration/process_control.py`:

```python
"""PID-lockfiles and cooperative stop-requests for the scheduler/watchdog
processes — dashboard-process-control-design.md.

Discovery and control are decoupled from who launched the process: any
process (the dashboard, or the scheduler/watchdog checking their own
state) reads/writes the same lockfiles, so this works whether a process
was started via the dashboard or manually in a terminal.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import psutil

from tradingsystem.config import REPO_ROOT

RUN_DIR = REPO_ROOT / "run"


def _pidfile(name: str) -> Path:
    return RUN_DIR / f"{name}.pid"


def _stopfile(name: str) -> Path:
    return RUN_DIR / f"{name}.stop_requested"


def write_pidfile(name: str) -> None:
    """Called by scheduler.py/watchdog.py on startup."""
    RUN_DIR.mkdir(exist_ok=True)
    _pidfile(name).write_text(str(os.getpid()))


def remove_pidfile(name: str) -> None:
    """Called by scheduler.py/watchdog.py on clean exit."""
    _pidfile(name).unlink(missing_ok=True)


@dataclasses.dataclass(frozen=True)
class ProcessStatus:
    name: str
    pid: int | None
    alive: bool


def get_process_status(name: str) -> ProcessStatus:
    """Reads the lockfile and verifies the PID is genuinely alive — a
    crashed process that didn't clean up its own lockfile reports as
    not-alive, not falsely "running"."""
    path = _pidfile(name)
    if not path.exists():
        return ProcessStatus(name=name, pid=None, alive=False)
    pid = int(path.read_text().strip())
    alive = psutil.pid_exists(pid)
    return ProcessStatus(name=name, pid=pid, alive=alive)


def request_stop(name: str) -> None:
    """Called by the dashboard's stop route."""
    RUN_DIR.mkdir(exist_ok=True)
    _stopfile(name).touch()


def is_stop_requested(name: str) -> bool:
    """Polled by scheduler.py/watchdog.py/cycle.py."""
    return _stopfile(name).exists()


def clear_stop_request(name: str) -> None:
    """Called by scheduler.py/watchdog.py once it has honored the request
    and is exiting, so a future restart doesn't immediately re-trigger it."""
    _stopfile(name).unlink(missing_ok=True)


def spawn_detached(module: str) -> int:
    """Starts `python -m tradingsystem.orchestration.<module>` as a
    detached subprocess that survives the dashboard's own process exiting.
    Returns the new process's PID."""
    process = subprocess.Popen(
        [sys.executable, "-m", f"tradingsystem.orchestration.{module}"],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        cwd=str(REPO_ROOT),
    )
    return process.pid


def force_kill(pid: int) -> None:
    """Called by the dashboard's stop route if graceful stop timed out."""
    try:
        psutil.Process(pid).kill()
    except psutil.NoSuchProcess:
        pass  # already gone
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_process_control.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Commit**

```bash
git add src/tradingsystem/orchestration/process_control.py tests/test_process_control.py pyproject.toml .gitignore
git commit -m "Add process_control module for PID-lockfiles and stop-requests"
```

---

### Task 2: `cycle.py`'s per-ticker stop-check

**Files:**
- Modify: `src/tradingsystem/orchestration/cycle.py`
- Test: `tests/test_cycle.py`

**Interfaces:**
- Consumes: `process_control.is_stop_requested(name: str) -> bool` (Task 1).
- Produces: no new public interface — this is a behavioral change to the existing per-ticker loop inside `run_full_cycle`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cycle.py`:

```python
def test_stop_requested_before_loop_skips_all_tickers(db_session, monkeypatch):
    from tradingsystem.orchestration import process_control
    monkeypatch.setattr(process_control, "is_stop_requested", lambda name: True)
    client = FakeAlpacaClient(market_status="open")

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=["AAPL", "MSFT"])

    assert db_session.query(AgentRun).filter(AgentRun.run_type == "pre_market").count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cycle.py::test_stop_requested_before_loop_skips_all_tickers -v`
Expected: FAIL — `AttributeError` or similar, since `cycle.py` doesn't yet import/use `process_control`, so patching it has no effect and both tickers still get researched (the test's assertion of `count() == 0` fails because the count is actually 2).

- [ ] **Step 3: Add the stop-check**

In `src/tradingsystem/orchestration/cycle.py`, replace:

```python
from tradingsystem.orchestration import discord_alerts, heartbeat, memory_ingestion, ticker_selection
```

with:

```python
from tradingsystem.orchestration import discord_alerts, heartbeat, memory_ingestion, process_control, ticker_selection
```

Then replace:

```python
    for ticker in watchlist:
        try:
```

with:

```python
    for ticker in watchlist:
        if process_control.is_stop_requested("scheduler"):
            log.info("stop requested — ending cycle early, %d ticker(s) skipped", len(watchlist) - watchlist.index(ticker))
            break
        try:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cycle.py::test_stop_requested_before_loop_skips_all_tickers -v`
Expected: PASS

- [ ] **Step 5: Run the full test_cycle.py file and the full suite to confirm nothing broke**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cycle.py -v`
Expected: all PASS, including every pre-existing test (none of them request a stop, so `is_stop_requested` returns its real value — `False`, since no `run/scheduler.stop_requested` file exists in the test environment — and every ticker in those tests still gets processed exactly as before).

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tradingsystem/orchestration/cycle.py tests/test_cycle.py
git commit -m "Add per-ticker stop-check to run_full_cycle"
```

---

### Task 3: Wire lockfiles, stop-checks, and logging into scheduler.py + watchdog.py; add run_once.py

**Files:**
- Modify: `src/tradingsystem/orchestration/scheduler.py`
- Modify: `src/tradingsystem/orchestration/watchdog.py`
- Create: `src/tradingsystem/orchestration/run_once.py`

**Interfaces:**
- Consumes: `process_control.write_pidfile`, `.remove_pidfile`, `.is_stop_requested`, `.clear_stop_request` (Task 1); `run_full_cycle` (existing, `cycle.py`).
- Produces: no new public interface for other tasks to consume — Task 4's dashboard routes call `process_control.spawn_detached("scheduler")` / `spawn_detached("watchdog")` / `spawn_detached("run_once")` directly, which only need these modules to be valid, importable entry points at those dotted-module paths (already true once this task creates/modifies them).

No dedicated automated tests in this task — real OS process lifecycle, matching this codebase's existing convention of not unit-testing thin process/OS-boundary code (e.g. `AlpacaClient`'s real methods have no direct test either). Verified instead via the real, brief manual check in Step 6 below.

- [ ] **Step 1: Update `scheduler.py` — imports and rotating file logging**

In `src/tradingsystem/orchestration/scheduler.py`, replace:

```python
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from tradingsystem.config import REPO_ROOT, Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.execution.alpaca_client import AlpacaClient
from tradingsystem.orchestration.cycle import run_full_cycle

log = logging.getLogger(__name__)

_TIMEZONE = "America/New_York"
```

with:

```python
from __future__ import annotations

import logging
import logging.handlers

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from tradingsystem.config import REPO_ROOT, Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.execution.alpaca_client import AlpacaClient
from tradingsystem.orchestration import process_control
from tradingsystem.orchestration.cycle import run_full_cycle

log = logging.getLogger(__name__)

_TIMEZONE = "America/New_York"
_PROCESS_NAME = "scheduler"
```

- [ ] **Step 2: Update `scheduler.py` — add the stop-check job to `build_scheduler`**

Replace:

```python
def build_scheduler(settings: Settings | None = None) -> BlockingScheduler:
    settings = settings or Settings()
    scheduler = BlockingScheduler(timezone=_TIMEZONE)
    scheduler.add_job(
        _run, CronTrigger.from_crontab(settings.pre_market_cron, timezone=_TIMEZONE),
        args=["pre_market"], id="pre_market_cycle",
    )
    scheduler.add_job(
        _run, CronTrigger.from_crontab(settings.midday_cron, timezone=_TIMEZONE),
        args=["midday"], id="midday_cycle",
    )
    return scheduler
```

with:

```python
def build_scheduler(settings: Settings | None = None) -> BlockingScheduler:
    settings = settings or Settings()
    scheduler = BlockingScheduler(timezone=_TIMEZONE)
    scheduler.add_job(
        _run, CronTrigger.from_crontab(settings.pre_market_cron, timezone=_TIMEZONE),
        args=["pre_market"], id="pre_market_cycle",
    )
    scheduler.add_job(
        _run, CronTrigger.from_crontab(settings.midday_cron, timezone=_TIMEZONE),
        args=["midday"], id="midday_cycle",
    )

    def _check_stop_requested() -> None:
        if process_control.is_stop_requested(_PROCESS_NAME):
            log.info("stop requested — shutting down scheduler")
            scheduler.shutdown(wait=False)

    scheduler.add_job(_check_stop_requested, IntervalTrigger(seconds=5), id="stop_check")
    return scheduler
```

- [ ] **Step 3: Update `scheduler.py` — write/remove the lockfile and configure logging in `__main__`**

Replace:

```python
if __name__ == "__main__":
    # TradingAgents' optional vendor modules (fred.py, alpha_vantage_common.py)
    # read API keys via bare os.getenv, bypassing our own Settings entirely —
    # pydantic-settings loads .env into Settings only, not into os.environ, so
    # this is required for FRED_API_KEY/ALPHA_VANTAGE_API_KEY in .env to reach
    # them. Scoped to __main__ so importing this module (e.g. in tests) never
    # mutates the process environment as a side effect.
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    logging.basicConfig(level=logging.INFO)
    build_scheduler().start()
```

with:

```python
if __name__ == "__main__":
    # TradingAgents' optional vendor modules (fred.py, alpha_vantage_common.py)
    # read API keys via bare os.getenv, bypassing our own Settings entirely —
    # pydantic-settings loads .env into Settings only, not into os.environ, so
    # this is required for FRED_API_KEY/ALPHA_VANTAGE_API_KEY in .env to reach
    # them. Scoped to __main__ so importing this module (e.g. in tests) never
    # mutates the process environment as a side effect.
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")

    process_control.RUN_DIR.mkdir(exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(logging.StreamHandler())
    root_logger.addHandler(logging.handlers.RotatingFileHandler(
        process_control.RUN_DIR / "scheduler.log", maxBytes=5_000_000, backupCount=3,
    ))

    process_control.write_pidfile(_PROCESS_NAME)
    try:
        build_scheduler().start()
    finally:
        process_control.remove_pidfile(_PROCESS_NAME)
        process_control.clear_stop_request(_PROCESS_NAME)
```

- [ ] **Step 4: Update `watchdog.py` — the same treatment**

In `src/tradingsystem/orchestration/watchdog.py`, replace:

```python
from __future__ import annotations

import datetime
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from tradingsystem.config import Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.orchestration import discord_alerts, heartbeat

log = logging.getLogger(__name__)

_TIMEZONE = "America/New_York"
_LOOKBACK = datetime.timedelta(days=8)
```

with:

```python
from __future__ import annotations

import datetime
import logging
import logging.handlers

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from tradingsystem.config import Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.orchestration import discord_alerts, heartbeat, process_control

log = logging.getLogger(__name__)

_TIMEZONE = "America/New_York"
_LOOKBACK = datetime.timedelta(days=8)
_PROCESS_NAME = "watchdog"
```

Replace:

```python
def build_watchdog(settings: Settings | None = None) -> BlockingScheduler:
    settings = settings or Settings()
    scheduler = BlockingScheduler(timezone=_TIMEZONE)

    def _run() -> None:
        session = make_session_factory()()
        try:
            check_heartbeat_staleness(session, settings)
        except Exception:
            log.critical("check_heartbeat_staleness raised unhandled — watchdog continuing", exc_info=True)
            session.rollback()
        finally:
            session.close()

    scheduler.add_job(
        _run, IntervalTrigger(minutes=settings.watchdog_check_interval_minutes), id="heartbeat_watchdog",
    )
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_watchdog().start()
```

with:

```python
def build_watchdog(settings: Settings | None = None) -> BlockingScheduler:
    settings = settings or Settings()
    scheduler = BlockingScheduler(timezone=_TIMEZONE)

    def _run() -> None:
        session = make_session_factory()()
        try:
            check_heartbeat_staleness(session, settings)
        except Exception:
            log.critical("check_heartbeat_staleness raised unhandled — watchdog continuing", exc_info=True)
            session.rollback()
        finally:
            session.close()

    scheduler.add_job(
        _run, IntervalTrigger(minutes=settings.watchdog_check_interval_minutes), id="heartbeat_watchdog",
    )

    def _check_stop_requested() -> None:
        if process_control.is_stop_requested(_PROCESS_NAME):
            log.info("stop requested — shutting down watchdog")
            scheduler.shutdown(wait=False)

    scheduler.add_job(_check_stop_requested, IntervalTrigger(seconds=5), id="stop_check")
    return scheduler


if __name__ == "__main__":
    process_control.RUN_DIR.mkdir(exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(logging.StreamHandler())
    root_logger.addHandler(logging.handlers.RotatingFileHandler(
        process_control.RUN_DIR / "watchdog.log", maxBytes=5_000_000, backupCount=3,
    ))

    process_control.write_pidfile(_PROCESS_NAME)
    try:
        build_watchdog().start()
    finally:
        process_control.remove_pidfile(_PROCESS_NAME)
        process_control.clear_stop_request(_PROCESS_NAME)
```

- [ ] **Step 5: Create `run_once.py`**

Create `src/tradingsystem/orchestration/run_once.py`:

```python
"""One-shot manual research/trade cycle — orchestration/cycle.py's
run_full_cycle, called once and exited. Independent of whether the
persistent scheduler process is running — no PID-lockfile of its own,
since it's expected to finish and exit on its own rather than be a
long-running process the dashboard tracks status for.

Entry point: python -m tradingsystem.orchestration.run_once
"""

from __future__ import annotations

import logging

from tradingsystem.config import Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.execution.alpaca_client import AlpacaClient
from tradingsystem.orchestration.cycle import run_full_cycle


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    session = make_session_factory()()
    client = AlpacaClient(settings)
    try:
        run_full_cycle(session, client, "manual", settings=settings)
    finally:
        session.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Manual verification — scheduler**

Run in a terminal: `.venv\Scripts\python.exe -m tradingsystem.orchestration.scheduler`

Within a few seconds, confirm in a **separate** terminal:
- `run/scheduler.pid` exists and contains a number matching the PID shown by `Get-Process python` (PowerShell) or Task Manager for the process you just started.
- `run/scheduler.log` exists and has at least one log line in it.

Then, from the separate terminal, request a stop:

PowerShell: `New-Item -ItemType File -Path run/scheduler.stop_requested -Force`

Within about 10 seconds (the 5-second stop-check interval plus some slack), confirm:
- The original terminal's scheduler process has exited (its prompt returns, or the process is gone from Task Manager).
- `run/scheduler.pid` no longer exists.
- `run/scheduler.stop_requested` no longer exists (cleared by the process on exit).

If the process does not exit within ~30 seconds, stop it manually (Ctrl+C in its terminal, or `Stop-Process` by PID) and report exactly what was observed instead of what was expected — do not silently mark this task done if the real behavior didn't match.

- [ ] **Step 7: Manual verification — watchdog**

Repeat Step 6's procedure for `python -m tradingsystem.orchestration.watchdog`, checking `run/watchdog.pid` / `run/watchdog.log` / `run/watchdog.stop_requested` instead.

- [ ] **Step 8: Run the full automated suite to confirm nothing broke**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all PASS (this task added no automated tests of its own, per the "no dedicated automated tests" note above — this step exists purely to confirm the import changes to `scheduler.py`/`watchdog.py` didn't break `tests/test_scheduler.py` or any other existing test).

- [ ] **Step 9: Commit**

```bash
git add src/tradingsystem/orchestration/scheduler.py src/tradingsystem/orchestration/watchdog.py src/tradingsystem/orchestration/run_once.py
git commit -m "Wire PID-lockfiles, stop-checks, and log capture into scheduler/watchdog; add run_once entry point"
```

---

### Task 4: Dashboard `/control` page and routes; docs update

**Files:**
- Modify: `src/tradingsystem/dashboard/app.py`
- Create: `src/tradingsystem/dashboard/templates/control.html`
- Modify: `src/tradingsystem/dashboard/templates/base.html` (nav link)
- Test: `tests/test_dashboard_control.py`
- Modify: `ARCHITECTURE.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `process_control.get_process_status`, `.request_stop`, `.spawn_detached`, `.force_kill` (Task 1).
- Produces: no new public interface — this is the final, user-facing task.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_control.py`:

```python
from tradingsystem.orchestration import process_control


def test_control_page_loads_when_nothing_running(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )

    response = client.get("/control")

    assert response.status_code == 200
    assert "scheduler" in response.text.lower()
    assert "watchdog" in response.text.lower()


def test_control_page_shows_alive_status(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=12345, alive=True),
    )

    response = client.get("/control")

    assert response.status_code == 200
    assert "12345" in response.text


def test_start_scheduler_refuses_if_already_alive(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=999, alive=True),
    )
    spawn_calls = []
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: spawn_calls.append(module) or -1)

    response = client.post("/control/scheduler/start")

    assert response.status_code == 409
    assert spawn_calls == []


def test_start_scheduler_spawns_when_not_alive(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )
    spawn_calls = []
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: spawn_calls.append(module) or 4242)

    response = client.post("/control/scheduler/start")

    assert response.status_code == 200
    assert spawn_calls == ["scheduler"]


def test_stop_scheduler_reports_clean_stop(client, monkeypatch):
    statuses = iter([
        process_control.ProcessStatus(name="scheduler", pid=555, alive=True),  # initial check before requesting
        process_control.ProcessStatus(name="scheduler", pid=555, alive=False),  # first poll after request: already gone
    ])
    monkeypatch.setattr(process_control, "get_process_status", lambda name: next(statuses))
    request_calls = []
    monkeypatch.setattr(process_control, "request_stop", lambda name: request_calls.append(name))
    force_kill_calls = []
    monkeypatch.setattr(process_control, "force_kill", lambda pid: force_kill_calls.append(pid))

    response = client.post("/control/scheduler/stop")

    assert response.status_code == 200
    assert request_calls == ["scheduler"]
    assert force_kill_calls == []  # clean stop, no forced kill needed
    assert "forced" not in response.text.lower()


def test_run_now_spawns_run_once(client, monkeypatch):
    spawn_calls = []
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: spawn_calls.append(module) or 7777)

    response = client.post("/control/run-now")

    assert response.status_code == 200
    assert spawn_calls == ["run_once"]


def test_control_page_handles_missing_log_files_gracefully(client, tmp_path, monkeypatch):
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)  # empty directory, no log files
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )

    response = client.get("/control")

    assert response.status_code == 200
    assert "no log yet" in response.text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_control.py -v`
Expected: FAIL — `404 Not Found` for `/control` and its sub-routes, since they don't exist yet.

- [ ] **Step 3: Add the nav link**

In `src/tradingsystem/dashboard/templates/base.html`, replace:

```html
    <nav>
        <a href="/">Overview</a>
        <a href="/decisions">Decisions</a>
        <a href="/orders">Orders</a>
        <a href="/pnl">P&amp;L</a>
    </nav>
```

with:

```html
    <nav>
        <a href="/">Overview</a>
        <a href="/decisions">Decisions</a>
        <a href="/orders">Orders</a>
        <a href="/pnl">P&amp;L</a>
        <a href="/control">Control</a>
    </nav>
```

- [ ] **Step 4: Create the `control.html` template**

Create `src/tradingsystem/dashboard/templates/control.html`:

```html
{% extends "base.html" %}
{% block title %}Control — Bot-Trading Dashboard{% endblock %}
{% block content %}
<h1>Control</h1>

<div class="card">
    <h2>Scheduler</h2>
    <p>
        Status: {% if scheduler_status.alive %}<span class="fresh">running</span> (PID {{ scheduler_status.pid }}){% else %}<span class="stale">stopped</span>{% endif %}
    </p>
    <form method="post" action="/control/scheduler/start" style="display:inline">
        <button type="submit" {% if scheduler_status.alive %}disabled{% endif %}>Start</button>
    </form>
    <form method="post" action="/control/scheduler/stop" style="display:inline">
        <button type="submit" {% if not scheduler_status.alive %}disabled{% endif %}>Stop</button>
    </form>
    {% if scheduler_stop_forced %}<p class="stale">Last stop required a forced kill — it did not exit cleanly within 60s.</p>{% endif %}
    <h3>Log (last 200 lines)</h3>
    <pre>{{ scheduler_log or "no log yet" }}</pre>
</div>

<div class="card">
    <h2>Watchdog</h2>
    <p>
        Status: {% if watchdog_status.alive %}<span class="fresh">running</span> (PID {{ watchdog_status.pid }}){% else %}<span class="stale">stopped</span>{% endif %}
    </p>
    <form method="post" action="/control/watchdog/start" style="display:inline">
        <button type="submit" {% if watchdog_status.alive %}disabled{% endif %}>Start</button>
    </form>
    <form method="post" action="/control/watchdog/stop" style="display:inline">
        <button type="submit" {% if not watchdog_status.alive %}disabled{% endif %}>Stop</button>
    </form>
    {% if watchdog_stop_forced %}<p class="stale">Last stop required a forced kill — it did not exit cleanly within 60s.</p>{% endif %}
    <h3>Log (last 200 lines)</h3>
    <pre>{{ watchdog_log or "no log yet" }}</pre>
</div>

<div class="card">
    <h2>Off-cycle run</h2>
    <p>Runs a single research/trade cycle right now, independent of the twice-daily schedule.</p>
    <form method="post" action="/control/run-now">
        <button type="submit">Run now</button>
    </form>
</div>
{% endblock %}
```

- [ ] **Step 5: Add the routes to `dashboard/app.py`**

Replace:

```python
from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, DebateTranscript, Decision, Order, PortfolioSnapshot, RealizedPnl
from tradingsystem.db.session import make_session_factory
from tradingsystem.orchestration import heartbeat as heartbeat_module
```

with:

```python
from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, DebateTranscript, Decision, Order, PortfolioSnapshot, RealizedPnl
from tradingsystem.db.session import make_session_factory
from tradingsystem.orchestration import heartbeat as heartbeat_module
from tradingsystem.orchestration import process_control
```

Then append (after the existing `pnl` route, at the end of the file):

```python
_STOP_POLL_INTERVAL_SECONDS = 2
_STOP_TIMEOUT_SECONDS = 60


def _tail_log(name: str, lines: int = 200) -> str | None:
    log_path = process_control.RUN_DIR / f"{name}.log"
    if not log_path.exists():
        return None
    return "\n".join(log_path.read_text().splitlines()[-lines:])


@app.get("/control")
def control(request: Request):
    return templates.TemplateResponse(
        request,
        "control.html",
        {
            "scheduler_status": process_control.get_process_status("scheduler"),
            "watchdog_status": process_control.get_process_status("watchdog"),
            "scheduler_log": _tail_log("scheduler"),
            "watchdog_log": _tail_log("watchdog"),
            "scheduler_stop_forced": False,
            "watchdog_stop_forced": False,
        },
    )


@app.post("/control/{name}/start")
def start_process(name: str):
    if name not in ("scheduler", "watchdog"):
        raise HTTPException(status_code=404, detail="Unknown process")
    if process_control.get_process_status(name).alive:
        raise HTTPException(status_code=409, detail=f"{name} is already running")
    pid = process_control.spawn_detached(name)
    return {"started": True, "pid": pid}


@app.post("/control/{name}/stop")
def stop_process(name: str):
    if name not in ("scheduler", "watchdog"):
        raise HTTPException(status_code=404, detail="Unknown process")
    status = process_control.get_process_status(name)
    if not status.alive:
        return {"stopped": True, "forced": False, "note": "was not running"}

    process_control.request_stop(name)
    waited = 0.0
    while waited < _STOP_TIMEOUT_SECONDS:
        time.sleep(_STOP_POLL_INTERVAL_SECONDS)
        waited += _STOP_POLL_INTERVAL_SECONDS
        if not process_control.get_process_status(name).alive:
            return {"stopped": True, "forced": False}

    process_control.force_kill(status.pid)
    process_control.remove_pidfile(name)
    return {"stopped": True, "forced": True}


@app.post("/control/run-now")
def run_now():
    pid = process_control.spawn_detached("run_once")
    return {"started": True, "pid": pid}
```

Add `import time` to the existing top-of-file imports — replace:

```python
import datetime
import pathlib
import uuid
```

with:

```python
import datetime
import pathlib
import time
import uuid
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_control.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 8: Update `ARCHITECTURE.md` and `README.md`'s read-only framing**

In `ARCHITECTURE.md` §7, find the sentence describing the dashboard as having "No write actions from the dashboard itself" (added when circuit-breaker clearing was deliberately kept out) and update it to note that process control (scheduler/watchdog start/stop, off-cycle run trigger) is now a dashboard capability, while config editing, order cancellation, circuit-breaker clearing, and the kill switch remain outside the dashboard. Add a short note that the dashboard's no-authentication decision (still localhost-only, single-operator) now carries more weight than when the dashboard was purely read-only, since it can affect the live trading process — flagged as a known trade-off, not resolved by this plan.

In `README.md`'s dashboard section, similarly update the "No write actions, no authentication" line to describe the new process-control capability, and add the `/control` page to the list of dashboard views/routes documented there.

Both edits should read naturally in context — read the current surrounding text in each file before writing the replacement, rather than guessing exact current wording.

- [ ] **Step 9: Commit**

```bash
git add src/tradingsystem/dashboard/app.py src/tradingsystem/dashboard/templates/control.html src/tradingsystem/dashboard/templates/base.html tests/test_dashboard_control.py ARCHITECTURE.md README.md
git commit -m "Add dashboard /control page for scheduler/watchdog process control"
```

---

## Final check

- [ ] Run the full suite once more: `.venv/Scripts/python.exe -m pytest -q` — expect all green.
- [ ] Confirm `run/` is gitignored and nothing under it was accidentally committed: `git status --porcelain run/` (should be empty/no output, whether or not the directory exists locally from manual verification in Task 3).
- [ ] Manually open `http://127.0.0.1:8787/control` (with `python -m tradingsystem.dashboard` running) and confirm the page loads and both Start buttons work end-to-end at least once, matching the design's intent — this is real integration verification beyond what the mocked unit tests in Task 4 cover.
