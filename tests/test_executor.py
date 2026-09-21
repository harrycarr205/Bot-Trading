import datetime
import uuid

import pytest
from alpaca.common.exceptions import APIError

from tradingsystem.db.models import AgentRun, Decision, Fill, Order, RealizedPnl
from tradingsystem.execution.alpaca_client import AccountSnapshot, OrderStatus, SubmittedOrder
from tradingsystem.execution.executor import place_order, sync_all_open_orders, sync_order_fills
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
        order_statuses=None,
    ):
        self.equity = equity
        self.cash = cash
        self.positions = positions or {}
        self.open_orders = open_orders or set()
        self.market_status = market_status
        self.submit_response = submit_response
        self.submit_error = submit_error
        self.order_status = order_status
        self.order_statuses = order_statuses or {}
        self.submit_calls = []

    def get_account(self):
        return AccountSnapshot(equity=self.equity, cash=self.cash)

    def get_positions(self):
        return self.positions

    def get_open_orders(self):
        return self.open_orders

    def get_clock(self):
        return self.market_status

    def get_latest_price(self, ticker):
        return 100.0

    def submit_limit_order(self, ticker, side, qty, limit_price):
        self.submit_calls.append((ticker, side, qty, limit_price))
        if self.submit_error is not None:
            raise self.submit_error
        return self.submit_response

    def get_order(self, alpaca_order_id):
        return self.order_statuses.get(alpaca_order_id, self.order_status)

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
    decision = Decision(agent_run_id=agent_run.id, rating="Buy", decision="buy", reasoning_summary="test")
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
    adv_notional=1_000_000.0,
    max_pct_of_adv=0.10,
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


def test_default_now_uses_utc_not_local(db_session, tmp_path):
    kill_switch_file = tmp_path / "KILL_SWITCH"
    client = FakeAlpacaClient(submit_response=SubmittedOrder(alpaca_order_id="utc1", status="new"))
    decision_id = make_decision(db_session)
    proposal = make_proposal(data_timestamp=datetime.datetime.utcnow())

    result = place_order(
        db_session, client, proposal, decision_id, str(kill_switch_file), **COMMON_KWARGS
    )

    assert result.submitted
    assert "stale_data" not in result.rejection_reasons


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


def test_sell_fill_computes_and_persists_realized_pnl(db_session):
    buy_decision_id = make_decision(db_session)
    buy_order = Order(
        decision_id=buy_decision_id, ticker="AAPL", side="buy", qty=10, limit_price=100.0,
        status="filled", alpaca_order_id="buy1", submitted_at=NOW,
    )
    db_session.add(buy_order)
    db_session.flush()
    db_session.add(Fill(order_id=buy_order.id, fill_price=100.0, fill_qty=10, filled_at=NOW))
    db_session.flush()

    sell_decision_id = make_decision(db_session)
    sell_order = Order(
        decision_id=sell_decision_id, ticker="AAPL", side="sell", qty=10, limit_price=110.0,
        status="new", alpaca_order_id="sell1", submitted_at=NOW,
    )
    db_session.add(sell_order)
    db_session.flush()

    sell_filled_at = NOW + datetime.timedelta(hours=1)
    client = FakeAlpacaClient(order_status=OrderStatus(
        alpaca_order_id="sell1", status="filled", filled_qty=10.0, filled_avg_price=110.0, filled_at=sell_filled_at,
    ))

    sync_order_fills(db_session, client, sell_order)

    realized = db_session.query(RealizedPnl).filter_by(ticker="AAPL").one()
    assert realized.pnl_amount == 100.0  # (110 - 100) * 10
    assert set(realized.decision_ids) == {buy_decision_id, sell_decision_id}
    assert realized.closed_at == sell_filled_at
    assert realized.entry_notional == 1000.0  # avg cost basis 100.0 * 10 shares sold


def test_buy_fill_does_not_create_realized_pnl(db_session):
    decision_id = make_decision(db_session)
    buy_order = Order(
        decision_id=decision_id, ticker="AAPL", side="buy", qty=10, limit_price=100.0,
        status="new", alpaca_order_id="buy2", submitted_at=NOW,
    )
    db_session.add(buy_order)
    db_session.flush()

    client = FakeAlpacaClient(order_status=OrderStatus(
        alpaca_order_id="buy2", status="filled", filled_qty=10.0, filled_avg_price=100.0, filled_at=NOW,
    ))

    sync_order_fills(db_session, client, buy_order)

    assert db_session.query(RealizedPnl).count() == 0


def test_sync_all_open_orders_skips_terminal_orders(db_session):
    decision_id = make_decision(db_session)

    open_order = Order(
        decision_id=decision_id, ticker="AAPL", side="buy", qty=10, limit_price=100.0,
        status="new", alpaca_order_id="open1", submitted_at=NOW,
    )
    filled_order = Order(
        decision_id=decision_id, ticker="AAPL", side="buy", qty=5, limit_price=100.0,
        status="filled", alpaca_order_id="filled1", submitted_at=NOW,
    )
    db_session.add(open_order)
    db_session.add(filled_order)
    db_session.flush()

    client = FakeAlpacaClient(order_statuses={
        "open1": OrderStatus(
            alpaca_order_id="open1", status="filled", filled_qty=10.0, filled_avg_price=101.0, filled_at=NOW,
        ),
        "filled1": OrderStatus(
            alpaca_order_id="filled1", status="filled", filled_qty=5.0, filled_avg_price=99.0, filled_at=NOW,
        ),
    })

    sync_all_open_orders(db_session, client)

    assert open_order.status == "filled"
    assert len(open_order.fills) == 1
    assert open_order.fills[0].fill_qty == 10.0
    # filled_order was already terminal — never polled, so its (identically
    # "fillable") canned response never gets applied. If this assertion
    # fails, sync_all_open_orders polled an order it shouldn't have.
    assert filled_order.fills == []
