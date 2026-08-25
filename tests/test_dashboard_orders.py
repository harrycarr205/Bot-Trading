import datetime

from tradingsystem.db.models import AgentRun, Decision, Fill, Order

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def test_orders_list_empty_state_does_not_500(client):
    response = client.get("/orders")

    assert response.status_code == 200
    assert "No orders recorded yet." in response.text


def test_orders_list_shows_order_and_fill(client, db_session):
    run = AgentRun(
        ticker="AAPL", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="decision_recorded",
    )
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="test")
    db_session.add(decision)
    db_session.flush()
    order = Order(
        decision_id=decision.id, ticker="AAPL", side="buy", qty=10, limit_price=150.0,
        status="filled", alpaca_order_id="abc123", submitted_at=NOW,
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(Fill(order_id=order.id, fill_price=149.5, fill_qty=10, filled_at=NOW))
    db_session.flush()

    response = client.get("/orders")

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "filled" in response.text
    assert "149.50" in response.text
