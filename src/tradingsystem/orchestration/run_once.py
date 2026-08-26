"""One-shot manual research/trade cycle — orchestration/cycle.py's
run_full_cycle, called once and exited. Independent of whether the
persistent scheduler process is running — no PID-lockfile of its own,
since it's expected to finish and exit on its own rather than be a
long-running process the dashboard tracks status for.

Entry point: python -m tradingsystem.orchestration.run_once
"""

from __future__ import annotations

import logging

from tradingsystem.config import Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.execution.alpaca_client import AlpacaClient
from tradingsystem.orchestration.cycle import run_full_cycle


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    session = make_session_factory()()
    client = AlpacaClient(settings)
    try:
        run_full_cycle(session, client, "manual", settings=settings)
    finally:
        session.close()


if __name__ == "__main__":
    main()
