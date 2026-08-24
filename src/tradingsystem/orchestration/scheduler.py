"""APScheduler wiring for the twice-daily research/trade cycle — ARCHITECTURE.md §6.

No business logic here — run_full_cycle (cycle.py) is framework-free and
independently testable; this module's only job is cron scheduling.

Entry point: python -m tradingsystem.orchestration.scheduler
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from tradingsystem.config import Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.execution.alpaca_client import AlpacaClient
from tradingsystem.orchestration.cycle import run_full_cycle

log = logging.getLogger(__name__)

_TIMEZONE = "America/New_York"


def _run(run_type: str) -> None:
    settings = Settings()
    session = make_session_factory()()
    client = AlpacaClient(settings)
    try:
        run_full_cycle(session, client, run_type, settings=settings)
    except Exception:
        log.critical("run_full_cycle(%s) raised unhandled — scheduler continuing", run_type, exc_info=True)
        session.rollback()
    finally:
        session.close()


def build_scheduler(settings: Settings | None = None) -> BlockingScheduler:
    settings = settings or Settings()
    scheduler = BlockingScheduler(timezone=_TIMEZONE)
    scheduler.add_job(
        _run, CronTrigger.from_crontab(settings.pre_market_cron, timezone=_TIMEZONE),
        args=["pre_market"], id="pre_market_cycle",
    )
    scheduler.add_job(
        _run, CronTrigger.from_crontab(settings.midday_cron, timezone=_TIMEZONE),
        args=["midday"], id="midday_cycle",
    )
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_scheduler().start()
