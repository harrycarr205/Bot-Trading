# Dashboard Process Control — Design

## Goal

Turn the dashboard from a passive viewer into a real control surface for the
scheduler and watchdog processes: start/stop both, see genuine live status
(not just last-heartbeat), trigger an off-cycle research run on demand, and
watch their logs — without weakening any of the safety-by-friction decisions
already made for circuit-breaker clearing and the kill switch (both stay
CLI-only, untouched by this work).

This is the first of two planned builds. A second, lighter pass will add
dashboard write-routes for editing `candidate_universe.yaml`,
`risk_config.yaml`, select `.env` settings, and order cancellation — those
are simple CRUD-style writes once the dashboard can accept writes at all,
and are explicitly **out of scope** here.

## Background

`scheduler.py` and `watchdog.py` are independent, long-running processes
today, each started manually in its own terminal window
(`python -m tradingsystem.orchestration.scheduler` /
`...watchdog`). Neither writes any record of its own PID anywhere. The
dashboard is a separate, on-demand, 100%-read-only FastAPI process with no
knowledge that either exists beyond reading the `scheduler_heartbeats`
table (a DB proxy for "did a cycle run recently," not "is the process
alive right now").

## Design

### PID-lockfiles and stop-requests: `orchestration/process_control.py` (new)

A new shared module, pure/testable functions plus the actual OS-level
spawn/kill calls:

```python
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
    crashed process that didn't clean up its own lockfile correctly
    reports as not-alive, not falsely "running"."""
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
    """Polled by scheduler.py/watchdog.py."""
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

`run/` is a new gitignored directory at the repo root (add `run/` to
`.gitignore`), created on first use by `write_pidfile`/`request_stop`
rather than required to exist upfront.

### Windows-specific spawn flags

This project targets Windows only (confirmed this session — no
cross-platform handling needed elsewhere in the codebase either).
`subprocess.DETACHED_PROCESS` and `subprocess.CREATE_NEW_PROCESS_GROUP`
are both real Windows-only `subprocess` module constants; no conditional
import logic is needed.

### Graceful-stop-with-timeout, honored at two levels

**Between cycles** (scheduler idle, waiting for the next cron trigger):
`build_scheduler()` in `scheduler.py` gains one more APScheduler job — a
frequent interval check (every 5 seconds) that calls
`process_control.is_stop_requested("scheduler")` and, if true, calls
`scheduler.shutdown(wait=False)`. This makes `BlockingScheduler.start()`
return control to the `if __name__ == "__main__":` block, which then
calls `remove_pidfile("scheduler")` and `clear_stop_request("scheduler")`
before exiting.

**Mid-cycle** (a research/trade cycle is actively running): `cycle.py`'s
`run_full_cycle` per-ticker loop checks
`process_control.is_stop_requested("scheduler")` at the very top of each
`for ticker in watchlist:` iteration — replace:

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

This never aborts a ticker already in progress — it only declines to
start the *next* one — so a stop request can never land mid-way through
submitting an order to Alpaca. Worst case, it waits for one ticker's full
research+trade sequence to finish (well under the dashboard's 60-second
timeout in ordinary operation).

**Watchdog** has no long-running "cycle" to protect mid-flight (its own
`check_heartbeat_staleness` call is a handful of fast DB/webhook calls,
not a multi-minute research pipeline), so it only needs the between-checks
form: `watchdog.py`'s `build_watchdog()` gets the same kind of frequent
stop-check interval job as the scheduler.

**Dashboard side**: the stop route calls `request_stop(name)`, then polls
`get_process_status(name).alive` every ~2 seconds for up to 60 seconds. If
it goes false within that window, report success. If not,
`force_kill(pid)`, delete the now-stale lockfile directly (the killed
process never got to clean up its own), and report that a forced kill was
needed (surfaced in the UI, not silently swallowed — an operator should
know a stop wasn't clean).

### Start

The dashboard's start route first checks `get_process_status(name).alive`
— if already alive, refuse (return an error, do not spawn a second
competing process; running two schedulers simultaneously risks duplicate
cycles and duplicate order submissions). Otherwise, call
`process_control.spawn_detached("scheduler")` (or `"watchdog"`) and report
the new PID.

### Logging: rotating file handlers, not raw subprocess redirection

`scheduler.py`'s and `watchdog.py`'s `if __name__ == "__main__":` blocks
currently call `logging.basicConfig(level=logging.INFO)`, which only
prints to stdout. Change to configure both a `StreamHandler` (stdout, so
running it manually in a terminal still shows output) and a
`RotatingFileHandler` pointed at `run/scheduler.log` /
`run/watchdog.log` (`maxBytes=5_000_000, backupCount=3` — bounded growth
for a 24/7 process, no operator tuning needed). `RUN_DIR.mkdir(exist_ok=True)`
before configuring the file handler.

### Off-cycle run: new `orchestration/run_once.py` entry point

A new, small standalone script — not a modification to `scheduler.py` —
so an off-cycle run works whether or not the persistent scheduler is
currently running, with no need to signal/coordinate with it:

```python
"""One-shot manual research/trade cycle — orchestration/cycle.py's
run_full_cycle, called once and exited. Independent of whether the
persistent scheduler process is running.

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

`run_type="manual"` needs no schema change (`AgentRun.run_type` is a plain
string column — the same pattern already used for `"stop_loss"`). The
dashboard's `/control/run-now` route calls
`process_control.spawn_detached("run_once")` — fire-and-forget, since
this process is expected to finish and exit on its own (no PID-lockfile
tracking needed for it, unlike the persistent scheduler/watchdog).

### Dashboard: new `/control` page and routes

A new dedicated page (`templates/control.html`), separate from the
existing read-only overview — keeps "here's the trading state" and
"here's the operational control surface" visually distinct, consistent
with the existing dashboard's per-concern view split.

Shows: scheduler status (alive/dead, PID, start/stop buttons), watchdog
status (same), an off-cycle "run now" button, and the last ~200 lines of
each process's log file (read directly from `run/scheduler.log` /
`run/watchdog.log` on each page load — no live-tail/websocket complexity
for this first pass). If neither process has ever been started, these log
files won't exist yet — the page shows "no log yet" instead of erroring.

New routes in `dashboard/app.py`:
- `GET /control` — the page itself
- `POST /control/scheduler/start`, `POST /control/scheduler/stop`
- `POST /control/watchdog/start`, `POST /control/watchdog/stop`
- `POST /control/run-now`

These are the first non-GET routes this dashboard has ever had — the
existing "no write actions" framing in `ARCHITECTURE.md`/`README.md`
needs updating once this ships, to describe write access as scoped to
process control only (still no config/order edits — that's the deferred
second build), and to note the no-authentication decision now carries
more weight than it did when the dashboard was purely read-only.

### Testing

`process_control.py`'s file-based logic (`write_pidfile`,
`get_process_status`, `request_stop`/`is_stop_requested`/
`clear_stop_request`) is testable directly against a `tmp_path`-based
`RUN_DIR` override. `spawn_detached`/`force_kill` (real subprocess
spawning/killing) are not meaningfully unit-testable — matches this
codebase's existing convention of not unit-testing thin OS/API wrapper
calls (e.g. `AlpacaClient`'s real methods). `cycle.py`'s new stop-check
gets a test using the existing `FakeAlpacaClient` pattern: request a stop
before the loop starts, assert only tickers before the stop point (or
zero, if requested before the loop begins) get `AgentRun` rows.

## Out of scope for this build

- Config editing (`candidate_universe.yaml`, `risk_config.yaml`, select
  `.env` settings) and order cancellation — the second, lighter build.
- Live-tailing logs via websocket/streaming — page-load log reads only.
- Authentication — explicitly not addressed here; flagged as a growing
  concern in the docs-update note above, not solved.
- Cross-platform process control — Windows-only, matching the rest of
  this codebase's deployment target.
