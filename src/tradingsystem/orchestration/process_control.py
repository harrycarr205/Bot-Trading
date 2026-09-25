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
    not-alive, not falsely "running". A corrupted or empty pidfile is
    treated the same way rather than raising, so a garbled file can never
    take down the dashboard's /control page."""
    path = _pidfile(name)
    if not path.exists():
        return ProcessStatus(name=name, pid=None, alive=False)
    try:
        pid = int(path.read_text().strip())
    except ValueError:
        return ProcessStatus(name=name, pid=None, alive=False)
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


# On the droplet every process is a systemd unit (deploy/systemd/), so the
# dashboard drives systemctl rather than spawning/killing PIDs itself — a
# spawned child would live in bot-dashboard's cgroup, and a killed one would
# just be restarted by Restart=on-failure. The dashboard runs as `deploy`;
# deploy/sudoers/bot-trading whitelists exactly the commands built below, so
# any change to their argv must be mirrored there.
_SYSTEMD_UNITS = {
    "scheduler": "bot-scheduler",
    "watchdog": "bot-watchdog",
    "run_once": "bot-run-once",
}
_SYSTEMCTL_TIMEOUT_SECONDS = 120


class SystemctlError(RuntimeError):
    """A `sudo -n systemctl ...` call exited non-zero (e.g. the sudoers
    file isn't installed, or the unit failed to start)."""


def running_under_systemd() -> bool:
    """True when this process was started by systemd, which sets
    INVOCATION_ID for every unit it runs — i.e. on the droplet, not on the
    Windows dev box where the detached-spawn path still applies."""
    return "INVOCATION_ID" in os.environ


def _systemctl(args: list[str]) -> None:
    cmd = ["sudo", "-n", "systemctl", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=_SYSTEMCTL_TIMEOUT_SECONDS)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise SystemctlError(f"{' '.join(cmd)} failed: {detail}")


def systemctl_start(name: str, *, block: bool = True) -> None:
    """`block=False` returns once the start job is queued — used for the
    oneshot bot-run-once unit, whose start would otherwise block until the
    whole research run finishes."""
    unit = _SYSTEMD_UNITS[name]
    _systemctl(["start", unit] if block else ["start", "--no-block", unit])


def systemctl_stop(name: str) -> None:
    """SIGTERM via systemd, which marks the unit stopped so
    Restart=on-failure doesn't bring it straight back."""
    _systemctl(["stop", _SYSTEMD_UNITS[name]])


def force_kill(pid: int) -> None:
    """Called by the dashboard's stop route if graceful stop timed out."""
    try:
        psutil.Process(pid).kill()
    except psutil.NoSuchProcess:
        pass  # already gone
