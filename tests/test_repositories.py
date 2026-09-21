import datetime
import uuid

from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, Decision, RealizedPnl
from tradingsystem.db.repositories import clear_active_breaker, get_realized_returns_by_rating

NOW = datetime.datetime(2026, 1, 5, 14, 30)


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


def make_decision(session, rating: str) -> uuid.UUID:
    agent_run = AgentRun(
        ticker="AAPL", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="decision_recorded",
    )
    session.add(agent_run)
    session.flush()
    decision = Decision(agent_run_id=agent_run.id, rating=rating, decision="buy", reasoning_summary="test")
    session.add(decision)
    session.flush()
    return decision.id


def test_get_realized_returns_by_rating_buckets_by_entry_rating(db_session):
    buy_decision_id = make_decision(db_session, "Buy")
    overweight_decision_id = make_decision(db_session, "Overweight")

    db_session.add(RealizedPnl(
        ticker="AAPL", decision_ids=[buy_decision_id], pnl_amount=100.0,
        entry_notional=1000.0, closed_at=NOW,
    ))
    db_session.add(RealizedPnl(
        ticker="MSFT", decision_ids=[overweight_decision_id], pnl_amount=-50.0,
        entry_notional=500.0, closed_at=NOW,
    ))
    db_session.flush()

    result = get_realized_returns_by_rating(db_session)

    assert result["Buy"] == [0.1]  # 100 / 1000
    assert result["Overweight"] == [-0.1]  # -50 / 500


def test_get_realized_returns_by_rating_excludes_rows_with_no_entry_notional(db_session):
    # A row written before 2026-09-03 — entry_notional left null (the
    # ALTER TABLE only adds the column, it doesn't backfill history).
    buy_decision_id = make_decision(db_session, "Buy")
    db_session.add(RealizedPnl(
        ticker="AAPL", decision_ids=[buy_decision_id], pnl_amount=100.0,
        entry_notional=None, closed_at=NOW,
    ))
    db_session.flush()

    result = get_realized_returns_by_rating(db_session)

    assert result["Buy"] == []


def test_get_realized_returns_by_rating_excludes_sell_and_underweight_ratings(db_session):
    # Only the sell-side decision that closed the position exists here — no
    # Buy/Overweight decision in decision_ids, so nothing to bucket.
    sell_decision_id = make_decision(db_session, "Sell")
    db_session.add(RealizedPnl(
        ticker="AAPL", decision_ids=[sell_decision_id], pnl_amount=100.0,
        entry_notional=1000.0, closed_at=NOW,
    ))
    db_session.flush()

    result = get_realized_returns_by_rating(db_session)

    assert result["Buy"] == []
    assert result["Overweight"] == []


def test_get_realized_returns_by_rating_double_counts_a_mixed_entry_round_trip(db_session):
    # Documented simplification: a round-trip whose decision_ids include both
    # a Buy and an Overweight entry contributes to both buckets.
    buy_decision_id = make_decision(db_session, "Buy")
    overweight_decision_id = make_decision(db_session, "Overweight")
    sell_decision_id = make_decision(db_session, "Sell")
    db_session.add(RealizedPnl(
        ticker="AAPL", decision_ids=[buy_decision_id, overweight_decision_id, sell_decision_id],
        pnl_amount=200.0, entry_notional=2000.0, closed_at=NOW,
    ))
    db_session.flush()

    result = get_realized_returns_by_rating(db_session)

    assert result["Buy"] == [0.1]
    assert result["Overweight"] == [0.1]


def test_get_realized_returns_by_rating_since_filters_older_rows(db_session):
    buy_decision_id = make_decision(db_session, "Buy")
    db_session.add(RealizedPnl(
        ticker="AAPL", decision_ids=[buy_decision_id], pnl_amount=100.0,
        entry_notional=1000.0, closed_at=NOW - datetime.timedelta(days=30),
    ))
    db_session.flush()

    result = get_realized_returns_by_rating(db_session, since=NOW - datetime.timedelta(days=1))

    assert result["Buy"] == []
