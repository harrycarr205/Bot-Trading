"""GET/POST /api/control/* — scheduler/watchdog process control (existing behavior,
moved under the JSON API — see ARCHITECTURE.md §7 for the stop/force-stop safety rationale)."""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends, HTTPException

from tradingsystem.dashboard.dependencies import require_same_origin
from tradingsystem.dashboard.schemas import ControlStatusResponse, ProcessStatusOut
from tradingsystem.orchestration import process_control

router = APIRouter()

_STOP_POLL_INTERVAL_SECONDS = 2
_STOP_TIMEOUT_SECONDS = 60
_START_POLL_INTERVAL_SECONDS = 0.5
_START_POLL_ATTEMPTS = 6  # ~3 seconds total

_start_lock = threading.Lock()


def _systemctl_or_500(action, *args, **kwargs) -> None:
    """Runs a process_control.systemctl_* call, turning a failed sudo/systemctl
    into a 500 carrying its stderr, so the Control page shows why (typically
    deploy/sudoers/bot-trading not installed) instead of a false success."""
    try:
        action(*args, **kwargs)
    except process_control.SystemctlError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _tail_log(name: str, lines: int = 200) -> str | None:
    log_path = process_control.RUN_DIR / f"{name}.log"
    if not log_path.exists():
        return None
    return "\n".join(log_path.read_text().splitlines()[-lines:])


@router.get("/control/status", response_model=ControlStatusResponse)
def control_status() -> ControlStatusResponse:
    scheduler = process_control.get_process_status("scheduler")
    watchdog = process_control.get_process_status("watchdog")
    return ControlStatusResponse(
        scheduler=ProcessStatusOut(alive=scheduler.alive, pid=scheduler.pid),
        watchdog=ProcessStatusOut(alive=watchdog.alive, pid=watchdog.pid),
        scheduler_log=_tail_log("scheduler"),
        watchdog_log=_tail_log("watchdog"),
        run_once_log=_tail_log("run_once"),
    )


@router.post("/control/{name}/start")
def start_process(name: str, _: None = Depends(require_same_origin)):
    if name not in ("scheduler", "watchdog"):
        raise HTTPException(status_code=404, detail="Unknown process")

    with _start_lock:
        if process_control.get_process_status(name).alive:
            raise HTTPException(status_code=409, detail=f"{name} is already running")

        process_control.clear_stop_request(name)
        if process_control.running_under_systemd():
            _systemctl_or_500(process_control.systemctl_start, name)
        else:
            process_control.spawn_detached(name)
        for _ in range(_START_POLL_ATTEMPTS):
            time.sleep(_START_POLL_INTERVAL_SECONDS)
            status = process_control.get_process_status(name)
            if status.alive:
                return {"started": True, "pid": status.pid}

    return {"started": True, "pid": None, "note": "spawned but not yet confirmed alive — refresh shortly"}


@router.post("/control/{name}/stop")
def stop_process(name: str, _: None = Depends(require_same_origin)):
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

    return {
        "stopped": False,
        "forced": False,
        "note": (
            f"did not stop within {_STOP_TIMEOUT_SECONDS}s — it may still be finishing an "
            "in-flight research cycle. Use Force Stop only if you're sure it's safe to kill "
            "(a forced kill mid-order-submission can leave an order live at the broker with "
            "no local record)."
        ),
    }


@router.post("/control/{name}/force-stop")
def force_stop_process(name: str, _: None = Depends(require_same_origin)):
    if name not in ("scheduler", "watchdog"):
        raise HTTPException(status_code=404, detail="Unknown process")

    status = process_control.get_process_status(name)
    if not status.alive:
        process_control.clear_stop_request(name)
        return {"stopped": True, "forced": False, "note": "was not running"}

    if process_control.running_under_systemd():
        # A bare kill would just be restarted by Restart=on-failure; stopping
        # the unit is what actually keeps it down.
        _systemctl_or_500(process_control.systemctl_stop, name)
    else:
        process_control.force_kill(status.pid)
    process_control.remove_pidfile(name)
    process_control.clear_stop_request(name)
    return {"stopped": True, "forced": True}


@router.post("/control/run-now")
def run_now(_: None = Depends(require_same_origin)):
    if process_control.running_under_systemd():
        # --no-block: the oneshot unit's start job otherwise waits for the whole
        # research run. A second press while one is running joins that job
        # rather than starting a parallel run.
        _systemctl_or_500(process_control.systemctl_start, "run_once", block=False)
        return {"started": True, "pid": None}
    pid = process_control.spawn_detached("run_once")
    return {"started": True, "pid": pid}
