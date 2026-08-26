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
    statuses = iter([
        process_control.ProcessStatus(name="scheduler", pid=None, alive=False),  # initial check before spawning
        process_control.ProcessStatus(name="scheduler", pid=4242, alive=True),   # first poll: now alive
    ])
    monkeypatch.setattr(process_control, "get_process_status", lambda name: next(statuses))
    spawn_calls = []
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: spawn_calls.append(module) or -1)

    response = client.post("/control/scheduler/start")

    assert response.status_code == 200
    assert spawn_calls == ["scheduler"]
    assert response.json()["pid"] == 4242  # from get_process_status, not spawn_detached's return value


def test_start_scheduler_reports_unconfirmed_if_never_alive(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: -1)

    response = client.post("/control/scheduler/start")

    assert response.status_code == 200
    assert response.json()["pid"] is None


def test_start_scheduler_holds_lock_across_check_and_spawn(client, monkeypatch):
    """Guards against the double-spawn race: a second concurrent request's
    alive-check must not be able to run until the first request's
    check-then-spawn-then-poll sequence has fully released the lock.

    Real thread-concurrency is flaky to assert on reliably, so this checks
    structurally (single-threaded) that every get_process_status/spawn_detached
    call the route makes happens while _start_lock is held — which is what
    actually prevents a second concurrent request from passing the
    alive-check before the first has spawned.
    """
    from tradingsystem.dashboard.app import _start_lock

    lock_states = []
    statuses = iter([
        process_control.ProcessStatus(name="scheduler", pid=None, alive=False),  # initial check
        process_control.ProcessStatus(name="scheduler", pid=4242, alive=True),   # first poll
    ])

    def fake_get_status(name):
        lock_states.append(_start_lock.locked())
        return next(statuses)

    def fake_spawn(module):
        lock_states.append(_start_lock.locked())
        return -1

    monkeypatch.setattr(process_control, "get_process_status", fake_get_status)
    monkeypatch.setattr(process_control, "spawn_detached", fake_spawn)

    response = client.post("/control/scheduler/start")

    assert response.status_code == 200
    assert lock_states == [True, True, True]  # check, spawn, and poll all ran under the lock
    assert not _start_lock.locked()  # released again once the request completed


def test_start_scheduler_rejects_mismatched_origin(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )
    spawn_calls = []
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: spawn_calls.append(module) or -1)

    response = client.post("/control/scheduler/start", headers={"origin": "http://evil.example"})

    assert response.status_code == 403
    assert spawn_calls == []


def test_run_now_rejects_mismatched_origin(client, monkeypatch):
    spawn_calls = []
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: spawn_calls.append(module) or 7777)

    response = client.post("/control/run-now", headers={"origin": "http://evil.example"})

    assert response.status_code == 403
    assert spawn_calls == []


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
    assert response.json()["forced"] is False  # clean stop, not a forced kill


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


def test_stop_scheduler_reports_still_running_after_timeout_without_force_killing(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=555, alive=True),  # never goes not-alive
    )
    monkeypatch.setattr(process_control, "request_stop", lambda name: None)
    force_kill_calls = []
    monkeypatch.setattr(process_control, "force_kill", lambda pid: force_kill_calls.append(pid))
    import tradingsystem.dashboard.app as app_module
    monkeypatch.setattr(app_module, "_STOP_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(app_module, "_STOP_POLL_INTERVAL_SECONDS", 0.1)

    response = client.post("/control/scheduler/stop")

    assert response.status_code == 200
    body = response.json()
    assert body["stopped"] is False
    assert force_kill_calls == []  # never force-killed automatically


def test_force_stop_scheduler_kills_and_clears_stop_request(client, monkeypatch):
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

    response = client.post("/control/scheduler/force-stop")

    assert response.status_code == 200
    body = response.json()
    assert body == {"stopped": True, "forced": True}
    assert force_kill_calls == [555]
    assert remove_calls == ["scheduler"]
    assert clear_calls == ["scheduler"]


def test_force_stop_when_not_alive_just_clears_stop_request(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )
    force_kill_calls = []
    monkeypatch.setattr(process_control, "force_kill", lambda pid: force_kill_calls.append(pid))
    clear_calls = []
    monkeypatch.setattr(process_control, "clear_stop_request", lambda name: clear_calls.append(name))

    response = client.post("/control/scheduler/force-stop")

    assert response.status_code == 200
    assert response.json() == {"stopped": True, "forced": False, "note": "was not running"}
    assert force_kill_calls == []
    assert clear_calls == ["scheduler"]


def test_force_stop_rejects_mismatched_origin(client, monkeypatch):
    force_kill_calls = []
    monkeypatch.setattr(process_control, "force_kill", lambda pid: force_kill_calls.append(pid))

    response = client.post("/control/scheduler/force-stop", headers={"origin": "http://evil.example"})

    assert response.status_code == 403
    assert force_kill_calls == []


def test_force_stop_unknown_process_name_404s(client):
    response = client.post("/control/nope/force-stop")

    assert response.status_code == 404


def test_start_scheduler_clears_any_stale_stop_request_before_spawning(client, monkeypatch):
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )
    monkeypatch.setattr(process_control, "spawn_detached", lambda module: -1)
    clear_calls = []
    monkeypatch.setattr(process_control, "clear_stop_request", lambda name: clear_calls.append(name))

    client.post("/control/scheduler/start")

    assert clear_calls == ["scheduler"]


def test_control_page_shows_run_once_log(client, monkeypatch, tmp_path):
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)
    (tmp_path / "run_once.log").write_text("2026-08-26 09:35:00 INFO run started\n")
    monkeypatch.setattr(
        process_control, "get_process_status",
        lambda name: process_control.ProcessStatus(name=name, pid=None, alive=False),
    )

    response = client.get("/control")

    assert response.status_code == 200
    assert "run started" in response.text
