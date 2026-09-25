import subprocess

import psutil
import pytest

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


def test_running_under_systemd_true_when_invocation_id_set(monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "0123456789abcdef")

    assert process_control.running_under_systemd() is True


def test_running_under_systemd_false_without_invocation_id(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)

    assert process_control.running_under_systemd() is False


class _FakeRun:
    """Records subprocess.run calls and returns a canned CompletedProcess."""

    def __init__(self, returncode=0, stderr=""):
        self.calls = []
        self._returncode = returncode
        self._stderr = stderr

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self._returncode, stdout="", stderr=self._stderr)


def test_systemctl_start_runs_exact_sudoers_whitelisted_command(monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(process_control.subprocess, "run", fake)

    process_control.systemctl_start("scheduler")

    assert fake.calls == [["sudo", "-n", "systemctl", "start", "bot-scheduler"]]


def test_systemctl_start_no_block_for_run_once(monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(process_control.subprocess, "run", fake)

    process_control.systemctl_start("run_once", block=False)

    assert fake.calls == [["sudo", "-n", "systemctl", "start", "--no-block", "bot-run-once"]]


def test_systemctl_stop_runs_exact_sudoers_whitelisted_command(monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(process_control.subprocess, "run", fake)

    process_control.systemctl_stop("watchdog")

    assert fake.calls == [["sudo", "-n", "systemctl", "stop", "bot-watchdog"]]


def test_systemctl_failure_raises_with_stderr(monkeypatch):
    monkeypatch.setattr(
        process_control.subprocess, "run",
        _FakeRun(returncode=1, stderr="sudo: a password is required\n"),
    )

    with pytest.raises(process_control.SystemctlError, match="sudo: a password is required"):
        process_control.systemctl_start("scheduler")


def test_systemctl_rejects_unknown_process_name(monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(process_control.subprocess, "run", fake)

    with pytest.raises(KeyError):
        process_control.systemctl_stop("postgres")
    assert fake.calls == []
