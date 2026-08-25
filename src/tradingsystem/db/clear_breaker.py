"""Manually clear a tripped circuit breaker.

ARCHITECTURE.md §4: circuit breaker reset is manual only, full review
required, no automated or agent-triggered clear. --cleared-by and --note
are both required so a real name and a real review note are always on
record for every clear.

Run with:
    python -m tradingsystem.db.clear_breaker daily --cleared-by "Harry" --note "reviewed today's drawdown, resuming"
    python -m tradingsystem.db.clear_breaker weekly --cleared-by "Harry" --note "..."
"""

from __future__ import annotations

import argparse
import sys

from tradingsystem.db.repositories import clear_active_breaker
from tradingsystem.db.session import make_session_factory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manually clear a tripped circuit breaker.")
    parser.add_argument("breaker_type", choices=["daily", "weekly"])
    parser.add_argument("--cleared-by", required=True, help="Your name — recorded on the cleared event.")
    parser.add_argument("--note", required=True, help="Review note — what you checked before clearing.")
    args = parser.parse_args(argv)

    session = make_session_factory()()
    try:
        event = clear_active_breaker(session, args.breaker_type, cleared_by=args.cleared_by, review_note=args.note)
        if event is None:
            print(f"No active {args.breaker_type} circuit breaker to clear.")
            return 1
        session.commit()
        print(f"Cleared {args.breaker_type} breaker (tripped {event.tripped_at}: {event.trigger_reason}).")
        print(f"cleared_by={event.cleared_by!r} review_note={event.review_note!r}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
