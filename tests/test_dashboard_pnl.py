import datetime

from tradingsystem.db.models import PortfolioSnapshot, RealizedPnl

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def test_pnl_empty_state_does_not_500(client, db_session):
    db_session.query(RealizedPnl).delete()
    db_session.query(PortfolioSnapshot).delete()
    db_session.flush()

    response = client.get("/pnl")

    assert response.status_code == 200
    assert "No portfolio snapshots recorded yet." in response.text
    assert "No realized P&amp;L recorded yet." in response.text


def test_pnl_shows_snapshots_and_realized(client, db_session):
    db_session.add(PortfolioSnapshot(
        snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0, positions={},
    ))
    db_session.flush()
    db_session.add(RealizedPnl(ticker="AAPL", decision_ids=[], pnl_amount=250.0, closed_at=NOW))
    db_session.flush()

    response = client.get("/pnl")

    assert response.status_code == 200
    assert "100,000.00" in response.text
    assert "250.00" in response.text
