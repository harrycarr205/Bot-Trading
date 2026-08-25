from tradingsystem.db.models import CircuitBreakerEvent
from tradingsystem.db.repositories import clear_active_breaker


def test_clear_active_breaker_clears_the_active_event(db_session):
    db_session.add(CircuitBreakerEvent(breaker_type="daily", trigger_reason="4% drawdown"))
    db_session.flush()

    cleared = clear_active_breaker(db_session, "daily", cleared_by="Harry", review_note="reviewed, resuming")

    assert cleared is not None
    assert cleared.cleared_at is not None
    assert cleared.cleared_by == "Harry"
    assert cleared.review_note == "reviewed, resuming"


def test_clear_active_breaker_returns_none_when_nothing_active(db_session):
    cleared = clear_active_breaker(db_session, "daily", cleared_by="Harry", review_note="nothing to clear")

    assert cleared is None


def test_clear_active_breaker_only_clears_the_matching_type(db_session):
    db_session.add(CircuitBreakerEvent(breaker_type="daily", trigger_reason="4% drawdown"))
    db_session.add(CircuitBreakerEvent(breaker_type="weekly", trigger_reason="9% drawdown"))
    db_session.flush()

    clear_active_breaker(db_session, "daily", cleared_by="Harry", review_note="reviewed")

    weekly = db_session.query(CircuitBreakerEvent).filter_by(breaker_type="weekly").one()
    assert weekly.cleared_at is None
