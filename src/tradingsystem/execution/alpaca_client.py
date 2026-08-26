"""Alpaca execution client — the only module allowed to talk to Alpaca's API.

ARCHITECTURE.md §2/§4: paper trading is the only mode until explicit written
sign-off. AlpacaClient enforces this at construction time, not just via config
default, so a config mistake can't silently place live orders.
"""

from __future__ import annotations

import dataclasses
import datetime
import zoneinfo
from typing import Protocol

from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from tradingsystem.config import Settings

_NY = zoneinfo.ZoneInfo("America/New_York")


@dataclasses.dataclass(frozen=True)
class AccountSnapshot:
    equity: float
    cash: float


@dataclasses.dataclass(frozen=True)
class SubmittedOrder:
    alpaca_order_id: str
    status: str


@dataclasses.dataclass(frozen=True)
class OrderStatus:
    alpaca_order_id: str
    status: str  # alpaca's raw order status string, e.g. "filled", "partially_filled"
    filled_qty: float
    filled_avg_price: float | None
    filled_at: datetime.datetime | None


@dataclasses.dataclass(frozen=True)
class PositionDetail:
    ticker: str
    qty: float
    avg_entry_price: float
    current_price: float


@dataclasses.dataclass(frozen=True)
class DailyBars:
    ticker: str
    closes: list[float]   # oldest to newest
    volumes: list[float]  # oldest to newest, same ordering as closes


def round_to_tick(price: float) -> float:
    """Round to Alpaca's minimum price increment.

    SEC Rule 612 (the sub-penny rule): $0.01 for prices >= $1, $0.0001 below
    $1. Alpaca's own last-trade data can report sub-penny prices (some
    venues print at finer granularity than the tick a retail limit order is
    allowed to use), so a raw current_price passed straight through as
    limit_price can get rejected — this is the fix, applied at the actual
    submission point so every caller is protected regardless of how the
    price was computed upstream.
    """
    decimals = 2 if price >= 1.0 else 4
    return round(price, decimals)


class AlpacaClientProtocol(Protocol):
    def get_account(self) -> AccountSnapshot: ...
    def get_positions(self) -> dict[str, float]: ...
    def get_position_details(self) -> list[PositionDetail]: ...
    def get_recent_daily_bars(self, tickers: list[str], lookback_days: int) -> dict[str, DailyBars]: ...
    def get_open_orders(self) -> set[tuple[str, str]]: ...
    def get_clock(self) -> str: ...
    def get_latest_price(self, ticker: str) -> float: ...
    def submit_limit_order(
        self, ticker: str, side: str, qty: float, limit_price: float
    ) -> SubmittedOrder: ...
    def get_order(self, alpaca_order_id: str) -> OrderStatus: ...
    def cancel_order(self, alpaca_order_id: str) -> None: ...


class AlpacaClient:
    """Real implementation, wrapping alpaca-py's TradingClient against paper only."""

    def __init__(self, settings: Settings | None = None):
        settings = settings or Settings()
        if settings.trading_mode != "paper":
            raise RuntimeError(
                f"trading_mode is '{settings.trading_mode}', not 'paper' — refusing to "
                "construct an AlpacaClient. Paper trading is the only mode until an "
                "explicit written sign-off (ARCHITECTURE.md §4/§8)."
            )
        self._client = TradingClient(
            api_key=settings.alpaca_paper_api_key,
            secret_key=settings.alpaca_paper_api_secret,
            paper=True,
        )
        # Market data is a separate alpaca-py client from the trading client.
        self._data_client = StockHistoricalDataClient(
            api_key=settings.alpaca_paper_api_key,
            secret_key=settings.alpaca_paper_api_secret,
        )

    def get_account(self) -> AccountSnapshot:
        account = self._client.get_account()
        return AccountSnapshot(equity=float(account.equity), cash=float(account.cash))

    def get_positions(self) -> dict[str, float]:
        positions = self._client.get_all_positions()
        return {p.symbol: float(p.market_value) for p in positions}

    def get_position_details(self) -> list[PositionDetail]:
        positions = self._client.get_all_positions()
        return [
            PositionDetail(
                ticker=p.symbol,
                qty=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
            )
            for p in positions
        ]

    def get_recent_daily_bars(self, tickers: list[str], lookback_days: int) -> dict[str, DailyBars]:
        # Always end at the most recently completed trading day — never
        # today's still-forming bar, which would understate relative volume
        # at this system's actual run times (9:35am/12:30pm ET, both
        # mid-trading-day).
        end_date = datetime.datetime.now(_NY).date() - datetime.timedelta(days=1)
        end = datetime.datetime.combine(end_date, datetime.time.min, tzinfo=_NY)
        start = end - datetime.timedelta(days=lookback_days * 2)  # padding for weekends/holidays
        request = StockBarsRequest(
            symbol_or_symbols=tickers,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
        )
        barset = self._data_client.get_stock_bars(request)
        result: dict[str, DailyBars] = {}
        for ticker, bars in barset.data.items():
            trimmed = bars[-lookback_days:]
            if len(trimmed) < 2:
                continue
            result[ticker] = DailyBars(
                ticker=ticker,
                closes=[float(b.close) for b in trimmed],
                volumes=[float(b.volume) for b in trimmed],
            )
        return result

    def get_open_orders(self) -> set[tuple[str, str]]:
        orders = self._client.get_orders()
        return {(o.symbol, o.side.value) for o in orders}

    def get_clock(self) -> str:
        clock = self._client.get_clock()
        return "open" if clock.is_open else "closed"

    def get_latest_price(self, ticker: str) -> float:
        trades = self._data_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=ticker)
        )
        return float(trades[ticker].price)

    def submit_limit_order(
        self, ticker: str, side: str, qty: float, limit_price: float
    ) -> SubmittedOrder:
        request = LimitOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=round_to_tick(limit_price),
        )
        order = self._client.submit_order(request)
        return SubmittedOrder(alpaca_order_id=str(order.id), status=order.status.value)

    def get_order(self, alpaca_order_id: str) -> OrderStatus:
        order = self._client.get_order_by_id(alpaca_order_id)
        return OrderStatus(
            alpaca_order_id=str(order.id),
            status=order.status.value,
            filled_qty=float(order.filled_qty or 0),
            filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price else None,
            filled_at=order.filled_at,
        )

    def cancel_order(self, alpaca_order_id: str) -> None:
        """Manual utility only — never called automatically by executor.py."""
        self._client.cancel_order_by_id(alpaca_order_id)
