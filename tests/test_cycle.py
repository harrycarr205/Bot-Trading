import datetime

from tradingsystem.config import RiskConfig
from tradingsystem.db.models import AgentRun, CircuitBreakerEvent, PortfolioSnapshot
from tradingsystem.execution.alpaca_client import AccountSnapshot
from tradingsystem.orchestration import cycle


class FakeAlpacaClient:
    def __init__(self, equity=100_000.0, cash=80_000.0, positions=None, open_orders=None,
                 market_status="open", price=100.0, price_overrides=None,
                 raise_on_price_for=None, submit_response=None):
        self.equity = equity
        self.cash = cash
        self.positions = positions or {}
        self.open_orders = open_orders or set()
        self.market_status = market_status
        self.price = price
        self.price_overrides = price_overrides or {}
        self.raise_on_price_for = raise_on_price_for or set()
        self.submit_response = submit_response
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
        if ticker in self.raise_on_price_for:
            raise RuntimeError(f"simulated price fetch failure for {ticker}")
        return self.price_overrides.get(ticker, self.price)

    def submit_limit_order(self, ticker, side, qty, limit_price):
        self.submit_calls.append((ticker, side, qty, limit_price))
        return self.submit_response

    def get_order(self, alpaca_order_id):
        raise NotImplementedError

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


def test_market_closed_journals_all_tickers_and_skips_everything_else(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(cycle.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    client = FakeAlpacaClient(market_status="closed")

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    runs = db_session.query(AgentRun).all()
    assert {r.ticker for r in runs} == set(WATCHLIST)
    assert all(r.outcome == "market_closed" for r in runs)
    assert db_session.query(PortfolioSnapshot).count() == 0
    assert alerts == []


def test_first_cycle_of_day_snapshots_baseline_and_does_not_trip(db_session):
    client = FakeAlpacaClient(market_status="open", equity=100_000.0)

    cycle.run_full_cycle(db_session, client, "pre_market", risk_config=RISK_CONFIG, watchlist=WATCHLIST)

    snapshots = db_session.query(PortfolioSnapshot).all()
    assert len(snapshots) == 1
    assert float(snapshots[0].equity) == 100_000.0
    assert db_session.query(AgentRun).count() == 0  # no skip-journal rows written; gate passed


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
