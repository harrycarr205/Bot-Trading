import datetime

from tradingsystem.db.models import CircuitBreakerEvent, PortfolioSnapshot, SchedulerHeartbeat

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def test_overview_empty_state_does_not_500(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.query(SchedulerHeartbeat).delete()
    db_session.query(CircuitBreakerEvent).delete()
    db_session.flush()

    response = client.get("/")

    assert response.status_code == 200
    assert "No portfolio snapshot recorded yet." in response.text
    assert "No heartbeat recorded yet" in response.text
    assert "No active circuit breakers." in response.text


def test_overview_shows_snapshot_and_fresh_heartbeat(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.query(SchedulerHeartbeat).delete()
    db_session.query(CircuitBreakerEvent).delete()
    db_session.flush()

    db_session.add(PortfolioSnapshot(
        snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0, positions={},
    ))
    db_session.add(SchedulerHeartbeat(
        component="scheduler", last_seen_at=datetime.datetime.utcnow(),
        last_run_type="pre_market", last_ticker="AAPL",
    ))
    db_session.flush()

    response = client.get("/")

    assert response.status_code == 200
    assert "100,000.00" in response.text
    assert "fresh" in response.text
    assert "AAPL" in response.text


def test_overview_shows_active_circuit_breaker(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.query(SchedulerHeartbeat).delete()
    db_session.query(CircuitBreakerEvent).delete()
    db_session.flush()

    db_session.add(CircuitBreakerEvent(breaker_type="daily", trigger_reason="4% drawdown"))
    db_session.flush()

    response = client.get("/")

    assert response.status_code == 200
    assert "daily" in response.text
    assert "4% drawdown" in response.text
