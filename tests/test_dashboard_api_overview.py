import datetime

from tradingsystem.db.models import CircuitBreakerEvent, PortfolioSnapshot, SchedulerHeartbeat


def test_api_overview_empty_state(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.query(SchedulerHeartbeat).delete()
    db_session.query(CircuitBreakerEvent).delete()
    db_session.flush()

    response = client.get("/api/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"] is None
    assert body["heartbeat"] is None
    assert body["active_breakers"] == []


def test_api_overview_shows_snapshot_and_fresh_heartbeat(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.query(SchedulerHeartbeat).delete()
    db_session.flush()
    db_session.add(PortfolioSnapshot(
        snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0, positions={},
    ))
    db_session.add(SchedulerHeartbeat(
        component="scheduler", last_seen_at=datetime.datetime.utcnow(),
        last_run_type="pre_market", last_ticker="AAPL",
    ))
    db_session.flush()

    response = client.get("/api/overview")

    body = response.json()
    assert body["snapshot"]["equity"] == 100000.0
    assert body["heartbeat_stale"] is False
    assert body["heartbeat"]["last_ticker"] == "AAPL"


def test_api_overview_shows_active_breaker(client, db_session):
    db_session.query(CircuitBreakerEvent).delete()
    db_session.flush()
    db_session.add(CircuitBreakerEvent(breaker_type="daily", trigger_reason="4% drawdown"))
    db_session.flush()

    response = client.get("/api/overview")

    body = response.json()
    assert body["active_breakers"][0]["breaker_type"] == "daily"
    assert body["active_breakers"][0]["trigger_reason"] == "4% drawdown"
