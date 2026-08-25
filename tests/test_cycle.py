import datetime

from tradingsystem.config import RiskConfig
from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, Decision, Order, PortfolioSnapshot
from tradingsystem.decision_engine import runner as runner_module
from tradingsystem.execution.alpaca_client import AccountSnapshot, OrderStatus
from tradingsystem.orchestration import cycle


class FakeAlpacaClient:
    def __init__(self, equity=100_000.0, cash=80_000.0, positions=None, open_orders=None,
                 market_status="open", price=100.0, price_overrides=None,
                 raise_on_price_for=None, submit_response=None, order_statuses=None,
                 position_details=None):
        self.equity = equity
        self.cash = cash
        self.positions = positions or {}
        self.open_orders = open_orders or set()
        self.market_status = market_status
        self.price = price
        self.price_overrides = price_overrides or {}
        self.raise_on_price_for = raise_on_price_for or set()
        self.submit_response = submit_response
        self.order_statuses = order_statuses or {}
        self.position_details = position_details or []
        self.submit_calls = []

    def get_account(self):
        return AccountSnapshot(equity=self.equity, cash=self.cash)

    def get_positions(self):
        return self.positions

    def get_position_details(self):
        return self.position_details

    def get_open_orders(self):
        return self.open_orders

    def get_clock(self):
        return self.market_status

    def get_latest_price(self, ticker):
        if ticker in self.raise_on_price_for:
            raise RuntimeError(f"simulated price fetch failure for {ticker}")
        return self.price_overrides.get(ticker, self.price)

    def submit_limit_order(self, ticker, side, qty, limit_price):
        self.submit_calls.append((ticker, side, qty, limit_price))
        return self.submit_response

    def get_order(self, alpaca_order_id):
        return self.order_statuses[alpaca_order_id]

    def cancel_order(self, alpaca_order_id):
        pass


RISK_CONFIG = RiskConfig(
    max_position_pct=0.10,
    cash_reserve_pct=0.20,
    stop_loss_pct=0.08,
    daily_drawdown_breaker_pct=0.03,
    weekly_drawdown_breaker_pct=0.08,
    stale_data_max_age_minutes=15,
)

WATCHLIST = ["AAPL", "MSFT"]


def make_final_state(final_trade_decision="Buy: strong fundamentals"):
    return {
        "market_report": "market report",
        "sentiment_report": "sentiment report",
        "news_report": "news report",
        "fundamentals_report": "fundamentals report",
        "investment_debate_state": {"bull_history": "bull", "bear_history": "bear", "judge_decision": "judge"},
        "trader_investment_plan": "trader plan",
        "risk_debate_state": {
            "aggressive_history": "aggressive", "conservative_history": "conservative",
            "neutral_history": "neutral", "judge_decision": "risk judge",
        },
        "investment_plan": "investment plan",
        "final_trade_decision": final_trade_decision,
    }


class ScriptedGraph:
    calls = []

    def __init__(self, debug=False, config=None):
        pass

    def propagate(self, ticker, trade_date):
        outcome = ScriptedGraph.calls.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_market_closed_journals_all_tickers_and_skips_everything_else(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    client = FakeAlpacaClient(market_status="closed")

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    runs = db_session.query(AgentRun).all()
    assert {r.ticker for r in runs} == set(WATCHLIST)
    assert all(r.outcome == "market_closed" for r in runs)
    assert db_session.query(PortfolioSnapshot).count() == 1
    assert float(db_session.query(PortfolioSnapshot).one().equity) == 100_000.0
    assert alerts == []


def test_first_cycle_of_day_snapshots_baseline_and_does_not_trip(db_session, monkeypatch):
    ScriptedGraph.calls = [
        (make_final_state("Hold: no clear edge"), "Hold"),
        (make_final_state("Hold: no clear edge"), "Hold"),
    ]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)
    client = FakeAlpacaClient(market_status="open", equity=100_000.0)

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    snapshots = db_session.query(PortfolioSnapshot).all()
    assert len(snapshots) == 1
    assert float(snapshots[0].equity) == 100_000.0
    assert db_session.query(AgentRun).count() == 2  # one per ticker; gate passed, loop ran
    assert all(d.decision == "hold" for d in db_session.query(Decision).all())
    assert db_session.query(Order).count() == 0


def test_active_daily_breaker_journals_and_skips_and_alerts(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    today = datetime.datetime.now(cycle._NY).date()
    db_session.add(PortfolioSnapshot(snapshot_date=today, equity=100_000.0, cash=80_000.0, positions={}))
    db_session.flush()
    client = FakeAlpacaClient(market_status="open", equity=96_000.0)  # 4% drawdown > 3% daily threshold

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    runs = db_session.query(AgentRun).all()
    assert {r.ticker for r in runs} == set(WATCHLIST)
    assert all(r.outcome == "circuit_breaker_active" for r in runs)
    events = db_session.query(CircuitBreakerEvent).all()
    assert len(events) == 1
    assert events[0].breaker_type == "daily"
    assert any(level == "critical" and "CIRCUIT BREAKER TRIPPED" in message for level, message in alerts)


def test_already_tripped_breaker_blocks_without_re_recording(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    today = datetime.datetime.now(cycle._NY).date()
    db_session.add(PortfolioSnapshot(snapshot_date=today, equity=100_000.0, cash=80_000.0, positions={}))
    db_session.add(CircuitBreakerEvent(breaker_type="daily", trigger_reason="already tripped earlier today"))
    db_session.flush()
    client = FakeAlpacaClient(market_status="open", equity=99_000.0)  # only 1% drawdown now, but breaker still active

    cycle.run_full_cycle(db_session, client, "midday", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    assert db_session.query(CircuitBreakerEvent).count() == 1  # not re-recorded
    runs = db_session.query(AgentRun).all()
    assert all(r.outcome == "circuit_breaker_active" for r in runs)


def test_hold_decision_places_no_order(db_session, monkeypatch):
    ScriptedGraph.calls = [(make_final_state("Hold: no clear edge"), "Hold")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)
    client = FakeAlpacaClient(market_status="open")

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=["AAPL"])

    assert db_session.query(Decision).filter_by(decision="hold").count() == 1
    assert db_session.query(Order).count() == 0
    assert client.submit_calls == []


def test_buy_decision_sizes_and_places_order(db_session, monkeypatch):
    ScriptedGraph.calls = [(make_final_state("Buy: strong fundamentals"), "Buy")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    from tradingsystem.execution.alpaca_client import SubmittedOrder
    client = FakeAlpacaClient(market_status="open", equity=100_000.0, price=100.0,
                               submit_response=SubmittedOrder(alpaca_order_id="abc123", status="new"))

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=["AAPL"])

    assert client.submit_calls == [("AAPL", "buy", 100, 100.0)]
    order = db_session.query(Order).filter_by(alpaca_order_id="abc123").one()
    assert order.ticker == "AAPL"
    assert any(level == "info" and "Order placed" in message for level, message in alerts)


def test_one_ticker_exception_does_not_stop_the_others(db_session, monkeypatch):
    ScriptedGraph.calls = [
        (make_final_state("Buy: strong fundamentals"), "Buy"),
        (make_final_state("Buy: strong fundamentals"), "Buy"),
    ]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    from tradingsystem.execution.alpaca_client import SubmittedOrder
    client = FakeAlpacaClient(market_status="open", equity=100_000.0, price=100.0,
                               raise_on_price_for={"AAPL"},
                               submit_response=SubmittedOrder(alpaca_order_id="xyz789", status="new"))

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=["AAPL", "MSFT"])

    assert client.submit_calls == [("MSFT", "buy", 100, 100.0)]
    assert db_session.query(Order).count() == 1
    assert any(level == "critical" and "AAPL" in message for level, message in alerts)


def test_stop_loss_triggers_full_exit_sell_and_alerts(db_session, monkeypatch):
    from tradingsystem.execution.alpaca_client import PositionDetail, SubmittedOrder
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    client = FakeAlpacaClient(
        market_status="open", equity=100_000.0,
        position_details=[PositionDetail(ticker="AAPL", qty=50, avg_entry_price=100.0, current_price=90.0)],
        submit_response=SubmittedOrder(alpaca_order_id="sl1", status="new"),
    )

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=[])

    assert client.submit_calls == [("AAPL", "sell", 50, 90.0)]
    order = db_session.query(Order).filter_by(alpaca_order_id="sl1").one()
    assert order.ticker == "AAPL"
    run = db_session.query(AgentRun).filter_by(run_type="stop_loss").one()
    assert run.ticker == "AAPL"
    decision = db_session.query(Decision).filter_by(agent_run_id=run.id).one()
    assert decision.rating == "Sell"
    assert decision.decision == "sell"
    assert any(level == "critical" and "STOP-LOSS" in message and "AAPL" in message for level, message in alerts)


def test_no_stop_loss_when_drawdown_below_threshold(db_session):
    from tradingsystem.execution.alpaca_client import PositionDetail
    client = FakeAlpacaClient(
        market_status="open", equity=100_000.0,
        position_details=[PositionDetail(ticker="AAPL", qty=50, avg_entry_price=100.0, current_price=95.0)],
    )

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=[])

    assert client.submit_calls == []
    assert db_session.query(AgentRun).filter_by(run_type="stop_loss").count() == 0


def test_stop_loss_fires_even_when_circuit_breaker_is_tripped(db_session, monkeypatch):
    from tradingsystem.execution.alpaca_client import PositionDetail, SubmittedOrder
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    today = datetime.datetime.now(cycle._NY).date()
    db_session.add(PortfolioSnapshot(snapshot_date=today, equity=100_000.0, cash=80_000.0, positions={}))
    db_session.flush()
    client = FakeAlpacaClient(
        market_status="open", equity=96_000.0,  # 4% drawdown > 3% daily threshold — breaker trips
        position_details=[PositionDetail(ticker="MSFT", qty=50, avg_entry_price=100.0, current_price=90.0)],
        submit_response=SubmittedOrder(alpaca_order_id="sl2", status="new"),
    )

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    assert client.submit_calls == [("MSFT", "sell", 50, 90.0)]
    assert db_session.query(CircuitBreakerEvent).count() == 1
    runs = db_session.query(AgentRun).filter(AgentRun.run_type != "stop_loss").all()
    assert all(r.outcome == "circuit_breaker_active" for r in runs)


def test_run_full_cycle_syncs_open_order_fills_every_run(db_session):
    now = datetime.datetime(2026, 1, 5, 14, 30)
    run = AgentRun(
        ticker="AAPL", run_type="pre_market", started_at=now, finished_at=now,
        market_status="open", outcome="decision_recorded",
    )
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="test")
    db_session.add(decision)
    db_session.flush()
    order = Order(
        decision_id=decision.id, ticker="AAPL", side="buy", qty=10, limit_price=100.0,
        status="new", alpaca_order_id="open1", submitted_at=now,
    )
    db_session.add(order)
    db_session.flush()

    # Market closed keeps this test focused on the fill-sync step, which
    # must run regardless of market status — it isn't part of the ticker
    # research loop, which is what the market-status gate short-circuits.
    client = FakeAlpacaClient(market_status="closed", order_statuses={
        "open1": OrderStatus(
            alpaca_order_id="open1", status="filled", filled_qty=10.0, filled_avg_price=101.0, filled_at=now,
        ),
    })

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    assert order.status == "filled"
    assert len(order.fills) == 1
    assert order.fills[0].fill_qty == 10.0
