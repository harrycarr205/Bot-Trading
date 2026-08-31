import datetime

from tradingsystem.db.models import PortfolioSnapshot, RealizedPnl


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
