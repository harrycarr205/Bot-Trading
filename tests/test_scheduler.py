import threading
import time

from apscheduler.triggers.cron import CronTrigger

from tradingsystem.config import Settings
from tradingsystem.orchestration import scheduler as scheduler_module
from tradingsystem.orchestration.scheduler import build_scheduler


def test_settings_default_cron_values():
    settings = Settings()
    assert settings.pre_market_cron == "35 9 * * mon-fri"
    assert settings.midday_cron == "30 12 * * mon-fri"


def test_build_scheduler_registers_two_cron_jobs():
    settings = Settings(pre_market_cron="0 8 * * mon-fri", midday_cron="30 12 * * mon-fri")
    scheduler = build_scheduler(settings)

    jobs = {job.id: job for job in scheduler.get_jobs()}
    # build_scheduler also registers a "stop_check" job (process_control
    # cooperative-shutdown polling) — assert the two cron jobs are present
    # rather than that they're the *only* jobs.
    assert {"pre_market_cycle", "midday_cycle"} <= set(jobs)
    assert isinstance(jobs["pre_market_cycle"].trigger, CronTrigger)
    assert isinstance(jobs["midday_cycle"].trigger, CronTrigger)
    assert jobs["pre_market_cycle"].args == ("pre_market",)
    assert jobs["midday_cycle"].args == ("midday",)


def test_wait_for_cycles_to_finish_returns_immediately_when_idle():
    scheduler_module._active_cycles = 0
    started = time.monotonic()

    scheduler_module._wait_for_cycles_to_finish(poll_seconds=5.0)

    assert time.monotonic() - started < 1.0  # never slept — would take 5s+ if it had


def test_wait_for_cycles_to_finish_blocks_until_active_cycles_reaches_zero():
    scheduler_module._active_cycles = 1
    finished = threading.Event()

    def waiter():
        scheduler_module._wait_for_cycles_to_finish(poll_seconds=0.05)
        finished.set()

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.2)
    assert not finished.is_set()  # still "in flight"

    with scheduler_module._cycle_lock:
        scheduler_module._active_cycles = 0
    thread.join(timeout=2.0)

    assert finished.is_set()


def test_run_increments_and_decrements_active_cycles(monkeypatch):
    from tradingsystem.config import Settings

    seen_during_run = []

    def fake_run_full_cycle(session, client, run_type, settings=None):
        seen_during_run.append(scheduler_module._active_cycles)

    monkeypatch.setattr(scheduler_module, "run_full_cycle", fake_run_full_cycle)
    monkeypatch.setattr(scheduler_module, "Settings", lambda: Settings())
    monkeypatch.setattr(scheduler_module, "make_session_factory", lambda: (lambda: _FakeSession()))
    monkeypatch.setattr(scheduler_module, "AlpacaClient", lambda settings: object())
    scheduler_module._active_cycles = 0

    scheduler_module._run("manual")

    assert seen_during_run == [1]  # was 1 (incremented) while run_full_cycle executed
    assert scheduler_module._active_cycles == 0  # decremented back after


class _FakeSession:
    def close(self):
        pass

    def rollback(self):
        pass


def test_cycles_never_run_concurrently(monkeypatch):
    # The 12:30 midday cycle used to start while the 9:35 cycle was still
    # running (each takes ~3.5h), doubling LLM load. Now it waits its turn.
    active = 0
    max_active = 0
    order = []
    guard = threading.Lock()

    def fake_cycle(session, client, run_type, settings=None):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        order.append(run_type)
        time.sleep(0.2)
        with guard:
            active -= 1

    monkeypatch.setattr(scheduler_module, "run_full_cycle", fake_cycle)
    monkeypatch.setattr(scheduler_module, "make_session_factory", lambda: (lambda: _NullSession()))
    monkeypatch.setattr(scheduler_module, "AlpacaClient", lambda settings: object())
    monkeypatch.setattr(scheduler_module.process_control, "is_stop_requested", lambda name: False)

    first = threading.Thread(target=scheduler_module._run, args=("pre_market",))
    second = threading.Thread(target=scheduler_module._run, args=("midday",))
    first.start()
    time.sleep(0.05)
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert max_active == 1
    assert order == ["pre_market", "midday"]


def test_queued_cycle_is_skipped_if_stop_requested_while_waiting(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler_module, "run_full_cycle", lambda *a, **k: calls.append(a[2]))
    monkeypatch.setattr(scheduler_module, "make_session_factory", lambda: (lambda: _NullSession()))
    monkeypatch.setattr(scheduler_module, "AlpacaClient", lambda settings: object())
    monkeypatch.setattr(scheduler_module.process_control, "is_stop_requested", lambda name: True)

    scheduler_module._run("midday")

    assert calls == []
    assert scheduler_module._active_cycles == 0


class _NullSession:
    def rollback(self):
        pass

    def close(self):
        pass
