import datetime

from tradingsystem.dashboard.app import app
from tradingsystem.dashboard.dependencies import get_alpaca_client
from tradingsystem.db.models import AgentRun, Decision


class FakePositionDetail:
    def __init__(self, ticker, qty, avg_entry_price, current_price):
        self.ticker = ticker
        self.qty = qty
        self.avg_entry_price = avg_entry_price
        self.current_price = current_price


class FakeAlpacaClient:
    def __init__(self, positions):
        self._positions = positions

    def get_position_details(self):
        return self._positions


def test_api_positions_computes_unrealized_pnl_and_links_latest_decision(client, db_session):
    now = datetime.datetime.utcnow()
    run = AgentRun(ticker="AAPL", run_type="pre_market", started_at=now, finished_at=now,
                    market_status="open", outcome="decision_recorded")
    db_session.add(run)
    db_session.flush()
    db_session.add(Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="x"))
    db_session.flush()

    fake = FakeAlpacaClient([FakePositionDetail("AAPL", 10.0, 180.0, 184.5)])
    app.dependency_overrides[get_alpaca_client] = lambda: fake

    response = client.get("/api/positions")

    body = response.json()
    pos = body["positions"][0]
    assert pos["ticker"] == "AAPL"
    assert pos["unrealized_pnl"] == 45.0
    assert pos["latest_decision"] == "buy"
    assert pos["latest_rating"] == "Buy"


def test_api_positions_marks_candidates_held_or_not(client, db_session):
    fake = FakeAlpacaClient([])
    app.dependency_overrides[get_alpaca_client] = lambda: fake

    response = client.get("/api/positions")

    body = response.json()
    assert isinstance(body["candidates"], list)
    assert all("ticker" in c and "held" in c for c in body["candidates"])
