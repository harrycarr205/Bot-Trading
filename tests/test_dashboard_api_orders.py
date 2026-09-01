import datetime

from tradingsystem.db.models import AgentRun, Decision, Fill, Order


def _make_order(db_session, status="new"):
    now = datetime.datetime.utcnow()
    run = AgentRun(ticker="AAPL", run_type="pre_market", started_at=now, finished_at=now,
                    market_status="open", outcome="decision_recorded")
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="x")
    db_session.add(decision)
    db_session.flush()
    order = Order(decision_id=decision.id, ticker="AAPL", side="buy", qty=10, limit_price=100.0,
                  status=status, alpaca_order_id="abc123", submitted_at=now)
    db_session.add(order)
    db_session.flush()
    return order


def test_api_orders_lists_with_nested_fills_and_cancellable_flag(client, db_session):
    order = _make_order(db_session, status="new")
    db_session.add(Fill(order_id=order.id, fill_price=101.0, fill_qty=10, filled_at=datetime.datetime.utcnow()))
    db_session.flush()

    response = client.get("/api/orders")

    body = response.json()
    row = next(o for o in body["orders"] if o["id"] == str(order.id))
    assert row["cancellable"] is True
    assert len(row["fills"]) == 1
    assert row["agent_run_id"] is not None


def test_api_orders_terminal_status_is_not_cancellable(client, db_session):
    _make_order(db_session, status="filled")

    response = client.get("/api/orders")

    body = response.json()
    assert all(o["cancellable"] is False for o in body["orders"] if o["status"] == "filled")


def test_api_orders_returns_empty_list_when_no_orders_exist(client, db_session):
    db_session.query(Fill).delete()
    db_session.query(Order).delete()
    db_session.query(Decision).delete()
    db_session.query(AgentRun).delete()
    db_session.flush()

    response = client.get("/api/orders")

    assert response.status_code == 200
    assert response.json()["orders"] == []
