import datetime
import uuid

from tradingsystem.db.models import AgentRun, Decision, PortfolioSnapshot, RealizedPnl


def test_api_pnl_resolves_decision_ids_to_linkable_agent_run_ids(client, db_session):
    """The decision-detail view is keyed by agent-run id, so a realized row's
    decision_ids alone cannot be linked to — agent_run_ids is what makes the
    P&L table's cross-link resolve instead of 404."""
    db_session.query(RealizedPnl).delete()
    db_session.flush()
    now = datetime.datetime.utcnow()
    run = AgentRun(ticker="AAPL", run_type="pre_market", started_at=now, finished_at=now,
                   market_status="open", outcome="decision_recorded")
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Sell", decision="sell", reasoning_summary="x")
    db_session.add(decision)
    db_session.flush()
    db_session.add(RealizedPnl(ticker="AAPL", decision_ids=[decision.id], pnl_amount=250.0, closed_at=now))
    db_session.flush()

    row = client.get("/api/pnl").json()["realized"][0]

    assert row["decision_ids"] == [str(decision.id)]
    assert row["agent_run_ids"] == [str(run.id)]


def test_api_pnl_skips_decision_ids_with_no_surviving_decision_row(client, db_session):
    db_session.query(RealizedPnl).delete()
    db_session.flush()
    db_session.add(RealizedPnl(ticker="AAPL", decision_ids=[uuid.uuid4()], pnl_amount=10.0,
                               closed_at=datetime.datetime.utcnow()))
    db_session.flush()

    row = client.get("/api/pnl").json()["realized"][0]

    assert row["agent_run_ids"] == []


def test_api_pnl_lists_snapshots_and_realized(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.query(RealizedPnl).delete()
    db_session.flush()
    db_session.add(PortfolioSnapshot(snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0, positions={}))
    db_session.add(RealizedPnl(ticker="AAPL", decision_ids=[], pnl_amount=250.0, closed_at=datetime.datetime.utcnow()))
    db_session.flush()

    response = client.get("/api/pnl")

    body = response.json()
    assert body["snapshots"][0]["equity"] == 100000.0
    assert body["realized"][0]["pnl_amount"] == 250.0


def test_api_pnl_returns_empty_lists_when_no_data_exists(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.query(RealizedPnl).delete()
    db_session.flush()

    response = client.get("/api/pnl")

    assert response.status_code == 200
    body = response.json()
    assert body["snapshots"] == []
    assert body["realized"] == []


def test_api_pnl_series_orders_points_by_date_ascending(client, db_session):
    db_session.query(PortfolioSnapshot).delete()
    db_session.flush()
    db_session.add(PortfolioSnapshot(snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0, positions={}))
    db_session.add(PortfolioSnapshot(snapshot_date=datetime.date(2026, 8, 25), equity=101_500.0, cash=80_000.0, positions={}))
    db_session.flush()

    response = client.get("/api/pnl/series")

    points = response.json()["points"]
    dates = [p["date"] for p in points]
    assert dates == sorted(dates)
