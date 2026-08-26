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


def test_get_process_status_corrupted_pidfile_reports_not_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)
    (tmp_path / "scheduler.pid").write_text("not-a-pid")

    status = process_control.get_process_status("scheduler")

    assert status.pid is None
    assert status.alive is False


def test_get_process_status_empty_pidfile_reports_not_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(process_control, "RUN_DIR", tmp_path)
    (tmp_path / "scheduler.pid").write_text("")

    status = process_control.get_process_status("scheduler")

    assert status.pid is None
    assert status.alive is False
