"""APScheduler wiring for the twice-daily research/trade cycle — ARCHITECTURE.md §6.

No business logic here — run_full_cycle (cycle.py) is framework-free and
independently testable; this module's only job is cron scheduling.

Entry point: python -m tradingsystem.orchestration.scheduler
"""

from __future__ import annotations

import logging
import logging.handlers

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from tradingsystem.config import REPO_ROOT, Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.execution.alpaca_client import AlpacaClient
from tradingsystem.orchestration import process_control
from tradingsystem.orchestration.cycle import run_full_cycle

log = logging.getLogger(__name__)

_TIMEZONE = "America/New_York"
_PROCESS_NAME = "scheduler"


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

    def _check_stop_requested() -> None:
        if process_control.is_stop_requested(_PROCESS_NAME):
            log.info("stop requested — shutting down scheduler")
            scheduler.shutdown(wait=False)

    scheduler.add_job(_check_stop_requested, IntervalTrigger(seconds=5), id="stop_check")
    return scheduler


if __name__ == "__main__":
    # TradingAgents' optional vendor modules (fred.py, alpha_vantage_common.py)
    # read API keys via bare os.getenv, bypassing our own Settings entirely —
    # pydantic-settings loads .env into Settings only, not into os.environ, so
    # this is required for FRED_API_KEY/ALPHA_VANTAGE_API_KEY in .env to reach
    # them. Scoped to __main__ so importing this module (e.g. in tests) never
    # mutates the process environment as a side effect.
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")

    process_control.RUN_DIR.mkdir(exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(logging.StreamHandler())
    root_logger.addHandler(logging.handlers.RotatingFileHandler(
        process_control.RUN_DIR / "scheduler.log", maxBytes=5_000_000, backupCount=3,
    ))

    process_control.write_pidfile(_PROCESS_NAME)
    try:
        build_scheduler().start()
    finally:
        process_control.remove_pidfile(_PROCESS_NAME)
        process_control.clear_stop_request(_PROCESS_NAME)
