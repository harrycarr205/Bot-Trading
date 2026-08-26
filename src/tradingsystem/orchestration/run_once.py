"""One-shot manual research/trade cycle — orchestration/cycle.py's
run_full_cycle, called once and exited. Independent of whether the
persistent scheduler process is running — no PID-lockfile of its own,
since it's expected to finish and exit on its own rather than be a
long-running process the dashboard tracks status for.

Does not write a scheduler heartbeat (record_heartbeat=False) — a manual
run refreshing the same heartbeat row the watchdog uses to detect the
persistent scheduler dying would mask a real scheduler-death alert during
any window a manual run happens to overlap.

Entry point: python -m tradingsystem.orchestration.run_once
"""

from __future__ import annotations

import logging
import logging.handlers

from tradingsystem.config import Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.execution.alpaca_client import AlpacaClient
from tradingsystem.orchestration import process_control
from tradingsystem.orchestration.cycle import run_full_cycle


def main() -> None:
    settings = Settings()
    session = make_session_factory()()
    client = AlpacaClient(settings)
    try:
        run_full_cycle(session, client, "manual", settings=settings, record_heartbeat=False)
    finally:
        session.close()


if __name__ == "__main__":
    # Same reasoning as scheduler.py's __main__ block: pydantic-settings
    # loads .env into Settings only, not into os.environ, but TradingAgents'
    # vendor modules read FRED_API_KEY/ALPHA_VANTAGE_API_KEY via bare
    # os.getenv. Scoped to __main__ so importing this module never mutates
    # the process environment as a side effect.
    from dotenv import load_dotenv

    from tradingsystem.config import REPO_ROOT

    load_dotenv(REPO_ROOT / ".env")

    process_control.RUN_DIR.mkdir(exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)
    file_handler = logging.handlers.RotatingFileHandler(
        process_control.RUN_DIR / "run_once.log", maxBytes=5_000_000, backupCount=3,
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    main()
