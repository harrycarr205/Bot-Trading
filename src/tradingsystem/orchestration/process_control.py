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
