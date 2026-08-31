from tradingsystem.orchestration import process_control


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
