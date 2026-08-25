"""Thin read/write helpers used by the orchestration layer (not yet built).

Only circuit-breaker event history is needed this pass: the risk/circuit_breaker.py
trip calculation is pure and DB-free; this is the persistence side of it.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from tradingsystem.db.models import CircuitBreakerEvent, Fill, Order


def get_active_breaker_event(session: Session, breaker_type: str) -> CircuitBreakerEvent | None:
    """Most recent event of this type that hasn't been manually cleared yet."""
    return (
        session.query(CircuitBreakerEvent)
        .filter(
            CircuitBreakerEvent.breaker_type == breaker_type,
            CircuitBreakerEvent.cleared_at.is_(None),
        )
        .order_by(CircuitBreakerEvent.tripped_at.desc())
        .first()
    )


def record_breaker_trip(
    session: Session, breaker_type: str, trigger_reason: str
) -> CircuitBreakerEvent:
    event = CircuitBreakerEvent(breaker_type=breaker_type, trigger_reason=trigger_reason)
    session.add(event)
    session.flush()
    return event


def clear_breaker_event(
    session: Session, event: CircuitBreakerEvent, cleared_by: str, review_note: str
) -> None:
    """Manual-only clear — no code path calls this without a human-supplied cleared_by/review_note."""
    event.cleared_at = datetime.datetime.utcnow()
    event.cleared_by = cleared_by
    event.review_note = review_note


def get_fills_for_ticker_before(session: Session, ticker: str, before: datetime.datetime) -> list[Fill]:
    """This ticker's fills strictly before `before`, chronologically ordered.

    Used by execution/realized_pnl.py's compute_realized_pnl to derive the
    average cost basis for a new sell fill from this system's own fill
    history, rather than Alpaca's live position data (which is racy to query
    once a closing sell has already landed).
    """
    return (
        session.query(Fill)
        .join(Order, Fill.order_id == Order.id)
        .filter(Order.ticker == ticker, Fill.filled_at < before)
        .order_by(Fill.filled_at.asc())
        .all()
    )


def clear_active_breaker(
    session: Session, breaker_type: str, cleared_by: str, review_note: str
) -> CircuitBreakerEvent | None:
    """Find and clear the active event of this type, if any.

    Returns the cleared event, or None if there was nothing active to clear.
    """
    event = get_active_breaker_event(session, breaker_type)
    if event is None:
        return None
    clear_breaker_event(session, event, cleared_by, review_note)
    return event
