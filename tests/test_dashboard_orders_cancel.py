import datetime
import uuid

from tradingsystem.dashboard.app import app
from tradingsystem.dashboard.dependencies import get_alpaca_client
from tradingsystem.db.models import AgentRun, Decision, Order


class FakeCancelAlpacaClient:
    def __init__(self, raise_on_cancel=False):
        self.cancel_calls = []
        self.raise_on_cancel = raise_on_cancel

    def cancel_order(self, alpaca_order_id):
        self.cancel_calls.append(alpaca_order_id)
        if self.raise_on_cancel:
            raise RuntimeError("simulated Alpaca rejection: order already filled")


def _make_order(db_session, status="new"):
    now = datetime.datetime.utcnow()
    run = AgentRun(ticker="AAPL", run_type="pre_market", started_at=now, finished_at=now,
                    market_status="open", outcome="decision_recorded")
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="test")
    db_session.add(decision)
    db_session.flush()
    order = Order(decision_id=decision.id, ticker="AAPL", side="buy", qty=10, limit_price=100.0,
                  status=status, alpaca_order_id="abc123", submitted_at=now)
    db_session.add(order)
    db_session.flush()
    return order


def test_cancel_open_order_succeeds(client, db_session):
    order = _make_order(db_session, status="new")
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/orders/{order.id}/cancel", follow_redirects=False)

    assert response.status_code == 303
    assert fake_client.cancel_calls == ["abc123"]


def test_cancel_terminal_order_returns_409_without_calling_alpaca(client, db_session):
    order = _make_order(db_session, status="filled")
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/orders/{order.id}/cancel")

    assert response.status_code == 409
    assert fake_client.cancel_calls == []


def test_cancel_missing_order_returns_404(client, db_session):
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/orders/{uuid.uuid4()}/cancel")

    assert response.status_code == 404


def test_cancel_surfaces_alpaca_rejection_as_409(client, db_session):
    order = _make_order(db_session, status="new")
    fake_client = FakeCancelAlpacaClient(raise_on_cancel=True)
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/orders/{order.id}/cancel")

    assert response.status_code == 409
    assert "already filled" in response.json()["detail"]


def test_cancel_rejects_mismatched_origin(client, db_session):
    order = _make_order(db_session, status="new")
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/orders/{order.id}/cancel", headers={"origin": "http://evil.example"})

    assert response.status_code == 403
    assert fake_client.cancel_calls == []


def test_orders_page_shows_cancel_button_only_for_open_orders(client, db_session):
    _make_order(db_session, status="new")

    response = client.get("/orders")

    assert response.status_code == 200
    assert "/cancel" in response.text
