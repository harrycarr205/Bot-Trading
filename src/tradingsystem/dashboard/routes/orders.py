"""GET /api/orders, POST /api/orders/{order_id}/cancel."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from tradingsystem.dashboard.dependencies import get_alpaca_client, get_db, require_same_origin
from tradingsystem.dashboard.schemas import FillOut, OrderOut, OrdersResponse
from tradingsystem.db.models import Order
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol
from tradingsystem.execution.executor import _TERMINAL_ORDER_STATUSES

router = APIRouter()


def _to_order_out(order: Order) -> OrderOut:
    return OrderOut(
        id=order.id, submitted_at=order.submitted_at, ticker=order.ticker, side=order.side,
        qty=order.qty, limit_price=order.limit_price, status=order.status,
        decision_id=order.decision_id, agent_run_id=order.decision.agent_run_id,
        fills=[FillOut.model_validate(f) for f in order.fills],
        cancellable=order.status not in _TERMINAL_ORDER_STATUSES,
    )


@router.get("/orders", response_model=OrdersResponse)
def list_orders(db: Session = Depends(get_db)) -> OrdersResponse:
    orders = db.query(Order).order_by(Order.submitted_at.desc()).all()
    return OrdersResponse(orders=[_to_order_out(o) for o in orders])


@router.post("/orders/{order_id}/cancel")
def cancel_order_route(
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    alpaca_client: AlpacaClientProtocol = Depends(get_alpaca_client),
    _: None = Depends(require_same_origin),
) -> dict:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status in _TERMINAL_ORDER_STATUSES:
        raise HTTPException(status_code=409, detail=f"order already {order.status}, nothing to cancel")
    try:
        alpaca_client.cancel_order(order.alpaca_order_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"cancelled": True}
