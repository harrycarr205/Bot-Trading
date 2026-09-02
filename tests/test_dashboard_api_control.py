import pytest

from tradingsystem.orchestration import process_control


@pytest.fixture
def isolated_run_dir(tmp_path, monkeypatch):
    """Points process_control.RUN_DIR at an empty tmp_path, so the log-tail
    tests never read the real run/*.log files a live scheduler is writing.
    Same pattern as tests/test_dashboard_api_config.py's isolated_config_files."""
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )
    return tmp_path


def test_api_control_status_returns_log_file_contents(client, isolated_run_dir):
    (isolated_run_dir / "scheduler.log").write_text("2026-08-26 09:35:00 INFO cycle started\n")
    (isolated_run_dir / "watchdog.log").write_text("2026-08-26 09:36:00 INFO watchdog check ok\n")
    (isolated_run_dir / "run_once.log").write_text("2026-08-26 09:40:00 INFO off-cycle run started\n")

    body = client.get("/api/control/status").json()

    assert "cycle started" in body["scheduler_log"]
    assert "watchdog check ok" in body["watchdog_log"]
    assert "off-cycle run started" in body["run_once_log"]


def test_api_control_status_returns_null_for_missing_log_files(client, isolated_run_dir):
    response = client.get("/api/control/status")

    assert response.status_code == 200
    body = response.json()
    assert body["scheduler_log"] is None
    assert body["watchdog_log"] is None
    assert body["run_once_log"] is None


def test_api_control_status_tails_only_the_last_lines(client, isolated_run_dir):
    (isolated_run_dir / "scheduler.log").write_text("\n".join(f"line {i}" for i in range(500)) + "\n")

    scheduler_log = client.get("/api/control/status").json()["scheduler_log"]

    lines = scheduler_log.splitlines()
    assert len(lines) == 200
    assert lines[0] == "line 300"
    assert lines[-1] == "line 499"


def test_api_control_start_reports_unconfirmed_when_process_never_reports_alive(client, monkeypatch):
    """The spawn succeeded but the process never showed up in the poll window —
    the operator must be told it is unconfirmed, not handed a bare success."""
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: -1)
    monkeypatch.setattr(process_control, "clear_stop_request", lambda name: None)
    import tradingsystem.dashboard.routes.control as control_module
    monkeypatch.setattr(control_module, "_START_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(control_module, "_START_POLL_ATTEMPTS", 2)

    response = client.post("/api/control/scheduler/start")

    assert response.status_code == 200
    assert response.json() == {
        "started": True,
        "pid": None,
        "note": "spawned but not yet confirmed alive — refresh shortly",
    }


def test_api_control_status_reports_alive_processes(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=12345, alive=True),
    )

    response = client.get("/api/control/status")

    body = response.json()
    assert body["scheduler"]["alive"] is True
    assert body["scheduler"]["pid"] == 12345


def test_api_control_start_refuses_if_already_alive(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=999, alive=True),
    )
    spawn_calls = []
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: spawn_calls.append(module) or -1)

    response = client.post("/api/control/scheduler/start")

    assert response.status_code == 409
    assert spawn_calls == []


def test_api_control_run_now_spawns_off_cycle_run(client, monkeypatch):
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: 4242)

    response = client.post("/api/control/run-now")

    assert response.status_code == 200
    assert response.json() == {"started": True, "pid": 4242}


def test_api_control_stop_reports_clean_stop(client, monkeypatch):
    statuses = iter([
        process_control.ProcessStatus(name="scheduler", pid=555, alive=True),
        process_control.ProcessStatus(name="scheduler", pid=555, alive=False),
    ])
    monkeypatch.setattr(process_control, "get_process_status", lambda name: next(statuses))
    request_calls = []
    monkeypatch.setattr(process_control, "request_stop", lambda name: request_calls.append(name))
    force_kill_calls = []
    monkeypatch.setattr(process_control, "force_kill", lambda pid: force_kill_calls.append(pid))

    response = client.post("/api/control/scheduler/stop")

    assert response.status_code == 200
    assert request_calls == ["scheduler"]
    assert force_kill_calls == []
    assert response.json()["forced"] is False


def test_api_control_stop_reports_still_running_after_timeout_without_force_killing(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=555, alive=True),
    )
    monkeypatch.setattr(process_control, "request_stop", lambda name: None)
    force_kill_calls = []
    monkeypatch.setattr(process_control, "force_kill", lambda pid: force_kill_calls.append(pid))
    import tradingsystem.dashboard.routes.control as control_module
    monkeypatch.setattr(control_module, "_STOP_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(control_module, "_STOP_POLL_INTERVAL_SECONDS", 0.1)

    response = client.post("/api/control/scheduler/stop")

    assert response.status_code == 200
    body = response.json()
    assert body["stopped"] is False
    assert force_kill_calls == []


def test_api_control_force_stop_kills_and_clears_stop_request(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=555, alive=True),
    )
    force_kill_calls = []
    monkeypatch.setattr(process_control, "force_kill", lambda pid: force_kill_calls.append(pid))
    remove_calls = []
    monkeypatch.setattr(process_control, "remove_pidfile", lambda name: remove_calls.append(name))
    clear_calls = []
    monkeypatch.setattr(process_control, "clear_stop_request", lambda name: clear_calls.append(name))

    response = client.post("/api/control/scheduler/force-stop")

    assert response.status_code == 200
    assert response.json() == {"stopped": True, "forced": True}
    assert force_kill_calls == [555]
    assert remove_calls == ["scheduler"]
    assert clear_calls == ["scheduler"]


def test_api_control_force_stop_when_not_alive_just_clears_stop_request(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )
    force_kill_calls = []
    monkeypatch.setattr(process_control, "force_kill", lambda pid: force_kill_calls.append(pid))
    clear_calls = []
    monkeypatch.setattr(process_control, "clear_stop_request", lambda name: clear_calls.append(name))

    response = client.post("/api/control/scheduler/force-stop")

    assert response.status_code == 200
    assert response.json() == {"stopped": True, "forced": False, "note": "was not running"}
    assert force_kill_calls == []
    assert clear_calls == ["scheduler"]


def test_api_control_force_stop_rejects_mismatched_origin(client, monkeypatch):
    force_kill_calls = []
    monkeypatch.setattr(process_control, "force_kill", lambda pid: force_kill_calls.append(pid))

    response = client.post("/api/control/scheduler/force-stop", headers={"origin": "http://evil.example"})

    assert response.status_code == 403
    assert force_kill_calls == []


def test_api_control_force_stop_unknown_process_name_404s(client):
    response = client.post("/api/control/nope/force-stop")

    assert response.status_code == 404


def test_api_control_start_rejects_mismatched_origin(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )
    spawn_calls = []
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: spawn_calls.append(module) or -1)

    response = client.post("/api/control/scheduler/start", headers={"origin": "http://evil.example"})

    assert response.status_code == 403
    assert spawn_calls == []


def test_api_control_run_now_rejects_mismatched_origin(client, monkeypatch):
    spawn_calls = []
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: spawn_calls.append(module) or 7777)

    response = client.post("/api/control/run-now", headers={"origin": "http://evil.example"})

    assert response.status_code == 403
    assert spawn_calls == []


def test_api_control_start_clears_any_stale_stop_request_before_spawning(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: -1)
    clear_calls = []
    monkeypatch.setattr(process_control, "clear_stop_request", lambda name: clear_calls.append(name))

    client.post("/api/control/scheduler/start")

    assert clear_calls == ["scheduler"]


def test_api_control_start_holds_lock_across_check_and_spawn(client, monkeypatch):
    """Guards against the double-spawn race: a second concurrent request's
    alive-check must not be able to run until the first request's
    check-then-spawn-then-poll sequence has fully released the lock."""
    from tradingsystem.dashboard.routes.control import _start_lock

    lock_states = []
    statuses = iter([
        process_control.ProcessStatus(name="scheduler", pid=None, alive=False),
        process_control.ProcessStatus(name="scheduler", pid=4242, alive=True),
    ])

    def fake_get_status(name):
        lock_states.append(_start_lock.locked())
        return next(statuses)

    def fake_spawn(module):
        lock_states.append(_start_lock.locked())
        return -1

    monkeypatch.setattr(process_control, "get_process_status", fake_get_status)
    monkeypatch.setattr(process_control, "spawn_detached", fake_spawn)
    monkeypatch.setattr(process_control, "clear_stop_request", lambda name: None)

    response = client.post("/api/control/scheduler/start")

    assert response.status_code == 200
    assert lock_states == [True, True, True]
    assert not _start_lock.locked()
