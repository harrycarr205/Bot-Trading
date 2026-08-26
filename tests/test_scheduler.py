from apscheduler.triggers.cron import CronTrigger

from tradingsystem.config import Settings
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
