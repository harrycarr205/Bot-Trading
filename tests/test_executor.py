import datetime
import uuid

import pytest
from alpaca.common.exceptions import APIError

from tradingsystem.db.models import AgentRun, Decision
from tradingsystem.execution.alpaca_client import AccountSnapshot, OrderStatus, SubmittedOrder
from tradingsystem.execution.executor import place_order, sync_order_fills
from tradingsystem.risk.validation import OrderProposal

NOW = datetime.datetime(2026, 1, 5, 14, 30)


class FakeAlpacaClient:
    def __init__(
        self,
        equity=100_000.0,
        cash=80_000.0,
        positions=None,
        open_orders=None,
        market_status="open",
        submit_response=None,
        submit_error=None,
        order_status=None,
    ):
        self.equity = equity
        self.cash = cash
        self.positions = positions or {}
        self.open_orders = open_orders or set()
        self.market_status = market_status
        self.submit_response = submit_response
        self.submit_error = submit_error
        self.order_status = order_status
        self.submit_calls = []

    def get_account(self):
        return AccountSnapshot(equity=self.equity, cash=self.cash)

    def get_positions(self):
        return self.positions

    def get_open_orders(self):
        return self.open_orders

    def get_clock(self):
        return self.market_status

    def submit_limit_order(self, ticker, side, qty, limit_price):
        self.submit_calls.append((ticker, side, qty, limit_price))
        if self.submit_error is not None:
            raise self.submit_error
        return self.submit_response

    def get_order(self, alpaca_order_id):
        return self.order_status

    def cancel_order(self, alpaca_order_id):
        pass


def make_decision(session) -> uuid.UUID:
    agent_run = AgentRun(
        ticker="AAPL",
        run_type="pre_market",
        started_at=NOW,
        finished_at=NOW,
        market_status="open",
        outcome="buy",
    )
    session.add(agent_run)
    session.flush()
    decision = Decision(agent_run_id=agent_run.id, decision="buy", reasoning_summary="test")
    session.add(decision)
    session.flush()
    return decision.id


def make_proposal(**overrides) -> OrderProposal:
    defaults = dict(
        ticker="AAPL",
        side="buy",
        order_type="limit",
        qty=1,
        limit_price=100.0,
        data_timestamp=NOW,
    )
    defaults.update(overrides)
    return OrderProposal(**defaults)


COMMON_KWARGS = dict(
    max_position_pct=0.10,
    cash_reserve_pct=0.20,
    stale_data_max_age_minutes=15,
)


def test_kill_switch_active_rejects_without_calling_alpaca(db_session, tmp_path):
    kill_switch_file = tmp_path / "KILL_SWITCH"
    kill_switch_file.write_text("halt")
    client = FakeAlpacaClient()
    decision_id = make_decision(db_session)

    result = place_order(
        db_session, client, make_proposal(), decision_id, str(kill_switch_file), **COMMON_KWARGS
    )

    assert not result.submitted
    assert "kill_switch_active" in result.rejection_reasons
    assert client.submit_calls == []


def test_validation_rejection_prevents_alpaca_call(db_session, tmp_path):
    kill_switch_file = tmp_path / "KILL_SWITCH"
    client = FakeAlpacaClient(market_status="closed")
    decision_id = make_decision(db_session)

    result = place_order(
        db_session, client, make_proposal(), decision_id, str(kill_switch_file), **COMMON_KWARGS
    )

    assert not result.submitted
    assert "market_status" in result.rejection_reasons
    assert client.submit_calls == []


def test_approved_order_submits_and_persists(db_session, tmp_path):
    kill_switch_file = tmp_path / "KILL_SWITCH"
    client = FakeAlpacaClient(submit_response=SubmittedOrder(alpaca_order_id="abc123", status="new"))
    decision_id = make_decision(db_session)

    result = place_order(
        db_session, client, make_proposal(), decision_id, str(kill_switch_file), **COMMON_KWARGS, now=NOW
    )

    assert result.submitted
    assert result.alpaca_order_id == "abc123"
    assert client.submit_calls == [("AAPL", "buy", 1, 100.0)]

    from tradingsystem.db.models import Order

    order = db_session.query(Order).filter_by(alpaca_order_id="abc123").one()
    assert order.ticker == "AAPL"
    assert order.status == "new"
    assert order.decision_id == decision_id


def test_alpaca_api_error_returns_error_result_no_retry(db_session, tmp_path):
    kill_switch_file = tmp_path / "KILL_SWITCH"
    client = FakeAlpacaClient(submit_error=APIError("simulated failure"))
    decision_id = make_decision(db_session)

    result = place_order(
        db_session, client, make_proposal(), decision_id, str(kill_switch_file), **COMMON_KWARGS, now=NOW
    )

    assert not result.submitted
    assert result.error is not None
    assert len(client.submit_calls) == 1  # attempted once, no retry


def test_sync_order_fills_persists_fill_and_updates_status(db_session, tmp_path):
    kill_switch_file = tmp_path / "KILL_SWITCH"
    client = FakeAlpacaClient(submit_response=SubmittedOrder(alpaca_order_id="xyz789", status="new"))
    decision_id = make_decision(db_session)

    place_order(db_session, client, make_proposal(), decision_id, str(kill_switch_file), **COMMON_KWARGS, now=NOW)

    from tradingsystem.db.models import Order

    order = db_session.query(Order).filter_by(alpaca_order_id="xyz789").one()

    client.order_status = OrderStatus(
        alpaca_order_id="xyz789",
        status="filled",
        filled_qty=1.0,
        filled_avg_price=99.5,
        filled_at=NOW,
    )
    sync_order_fills(db_session, client, order)

    assert order.status == "filled"
    assert len(order.fills) == 1
    assert order.fills[0].fill_qty == 1.0
    assert order.fills[0].fill_price == 99.5
