"""Thin read/write helpers used by the orchestration layer (not yet built).

Only circuit-breaker event history is needed this pass: the risk/circuit_breaker.py
trip calculation is pure and DB-free; this is the persistence side of it.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from tradingsystem.db.models import CircuitBreakerEvent, Decision, Fill, Order, RealizedPnl


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


def get_realized_returns_by_rating(
    session: Session, since: datetime.datetime | None = None
) -> dict[str, list[float]]:
    """% return per closed round-trip, bucketed by entry rating (Buy/Overweight).

    Used by risk/kelly_sizing.py's compute_half_kelly_target_fraction. Only
    RealizedPnl rows with a non-null entry_notional are usable (rows written
    before entry_notional existed are silently excluded, never imputed).

    Simplification: RealizedPnl.decision_ids is stored sorted by UUID string
    (execution/realized_pnl.py), not chronologically, so "the rating that
    opened the position" isn't reliably recoverable — a round-trip whose
    decision_ids include both a Buy- and an Overweight-rated decision
    contributes its return to both buckets. Documented, not a bug.
    """
    query = session.query(RealizedPnl).filter(RealizedPnl.entry_notional.isnot(None))
    if since is not None:
        query = query.filter(RealizedPnl.closed_at >= since)
    rows = query.all()

    buckets: dict[str, list[float]] = {"Buy": [], "Overweight": []}
    for row in rows:
        pct_return = float(row.pnl_amount) / float(row.entry_notional)
        entry_decisions = (
            session.query(Decision)
            .filter(Decision.id.in_(row.decision_ids), Decision.rating.in_(("Buy", "Overweight")))
            .all()
        )
        for decision in entry_decisions:
            buckets[decision.rating].append(pct_return)
    return buckets
