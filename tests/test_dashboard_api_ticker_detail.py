import datetime

from tradingsystem.db.models import AgentRun, Decision, Order


def test_api_ticker_detail_returns_runs_and_orders_for_ticker(client, db_session):
    now = datetime.datetime.utcnow()
    run = AgentRun(ticker="AAPL", run_type="pre_market", started_at=now, finished_at=now,
                    market_status="open", outcome="decision_recorded")
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="x")
    db_session.add(decision)
    db_session.flush()
    db_session.add(Order(decision_id=decision.id, ticker="AAPL", side="buy", qty=10,
                          limit_price=100.0, status="filled", alpaca_order_id="abc", submitted_at=now))
    db_session.flush()

    response = client.get("/api/ticker/AAPL")

    body = response.json()
    assert body["ticker"] == "AAPL"
    assert len(body["runs"]) == 1
    assert len(body["orders"]) == 1


def test_api_ticker_detail_empty_for_unknown_ticker(client, db_session):
    response = client.get("/api/ticker/ZZZZ")
    body = response.json()
    assert body["runs"] == []
    assert body["orders"] == []
