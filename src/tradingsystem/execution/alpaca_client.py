"""Alpaca execution client — the only module allowed to talk to Alpaca's API.

ARCHITECTURE.md §2/§4: paper trading is the only mode until explicit written
sign-off. AlpacaClient enforces this at construction time, not just via config
default, so a config mistake can't silently place live orders.
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Protocol

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from tradingsystem.config import Settings


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


class AlpacaClientProtocol(Protocol):
    def get_account(self) -> AccountSnapshot: ...
    def get_positions(self) -> dict[str, float]: ...
    def get_open_orders(self) -> set[tuple[str, str]]: ...
    def get_clock(self) -> str: ...
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

    def get_account(self) -> AccountSnapshot:
        account = self._client.get_account()
        return AccountSnapshot(equity=float(account.equity), cash=float(account.cash))

    def get_positions(self) -> dict[str, float]:
        positions = self._client.get_all_positions()
        return {p.symbol: float(p.market_value) for p in positions}

    def get_open_orders(self) -> set[tuple[str, str]]:
        orders = self._client.get_orders()
        return {(o.symbol, o.side.value) for o in orders}

    def get_clock(self) -> str:
        clock = self._client.get_clock()
        return "open" if clock.is_open else "closed"

    def submit_limit_order(
        self, ticker: str, side: str, qty: float, limit_price: float
    ) -> SubmittedOrder:
        request = LimitOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price,
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
