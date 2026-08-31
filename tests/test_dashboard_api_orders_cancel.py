import uuid

from tradingsystem.dashboard.app import app
from tradingsystem.dashboard.dependencies import get_alpaca_client
from tests.test_dashboard_api_orders import _make_order


class FakeCancelAlpacaClient:
    def __init__(self, raise_on_cancel=False):
        self.cancel_calls = []
        self.raise_on_cancel = raise_on_cancel

    def cancel_order(self, alpaca_order_id):
        self.cancel_calls.append(alpaca_order_id)
        if self.raise_on_cancel:
            raise RuntimeError("simulated Alpaca rejection: order already filled")


def test_api_cancel_open_order_succeeds(client, db_session):
    order = _make_order(db_session, status="new")
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/api/orders/{order.id}/cancel")

    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    assert fake_client.cancel_calls == ["abc123"]


def test_api_cancel_terminal_order_returns_409(client, db_session):
    order = _make_order(db_session, status="filled")
    app.dependency_overrides[get_alpaca_client] = lambda: FakeCancelAlpacaClient()

    response = client.post(f"/api/orders/{order.id}/cancel")

    assert response.status_code == 409


def test_api_cancel_missing_order_returns_404(client, db_session):
    app.dependency_overrides[get_alpaca_client] = lambda: FakeCancelAlpacaClient()

    response = client.post(f"/api/orders/{uuid.uuid4()}/cancel")

    assert response.status_code == 404


def test_api_cancel_surfaces_alpaca_rejection_as_409(client, db_session):
    order = _make_order(db_session, status="new")
    app.dependency_overrides[get_alpaca_client] = lambda: FakeCancelAlpacaClient(raise_on_cancel=True)

    response = client.post(f"/api/orders/{order.id}/cancel")

    assert response.status_code == 409
    assert "already filled" in response.json()["detail"]


def test_api_cancel_rejects_mismatched_origin(client, db_session):
    order = _make_order(db_session, status="new")
    fake_client = FakeCancelAlpacaClient()
    app.dependency_overrides[get_alpaca_client] = lambda: fake_client

    response = client.post(f"/api/orders/{order.id}/cancel", headers={"origin": "http://evil.example"})

    assert response.status_code == 403
    assert fake_client.cancel_calls == []
