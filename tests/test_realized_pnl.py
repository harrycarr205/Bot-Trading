import datetime
import uuid

from tradingsystem.execution.realized_pnl import FillRecord, compute_realized_pnl

NOW = datetime.datetime(2026, 1, 5, 14, 30)
BUY_DECISION = uuid.uuid4()
BUY_DECISION_2 = uuid.uuid4()
SELL_DECISION = uuid.uuid4()


def make_buy(price, qty, decision_id=BUY_DECISION, filled_at=NOW):
    return FillRecord(side="buy", price=price, qty=qty, decision_id=decision_id, filled_at=filled_at)


def make_sell(price, qty, decision_id=SELL_DECISION, filled_at=NOW):
    return FillRecord(side="sell", price=price, qty=qty, decision_id=decision_id, filled_at=filled_at)


def test_no_prior_fills_returns_none():
    assert compute_realized_pnl([], sell_price=110.0, sell_qty=10, sell_decision_id=SELL_DECISION) is None


def test_single_buy_full_sell_computes_pnl():
    prior = [make_buy(100.0, 10)]
    result = compute_realized_pnl(prior, sell_price=110.0, sell_qty=10, sell_decision_id=SELL_DECISION)
    assert result.pnl_amount == 100.0  # (110 - 100) * 10
    assert set(result.decision_ids) == {BUY_DECISION, SELL_DECISION}


def test_multiple_buys_use_weighted_average_cost():
    prior = [make_buy(100.0, 10, decision_id=BUY_DECISION), make_buy(120.0, 10, decision_id=BUY_DECISION_2)]
    # weighted avg = (100*10 + 120*10) / 20 = 110
    result = compute_realized_pnl(prior, sell_price=115.0, sell_qty=20, sell_decision_id=SELL_DECISION)
    assert result.pnl_amount == 100.0  # (115 - 110) * 20
    assert set(result.decision_ids) == {BUY_DECISION, BUY_DECISION_2, SELL_DECISION}


def test_single_buy_full_sell_computes_entry_notional():
    prior = [make_buy(100.0, 10)]
    result = compute_realized_pnl(prior, sell_price=110.0, sell_qty=10, sell_decision_id=SELL_DECISION)
    assert result.entry_notional == 1000.0  # avg cost basis 100.0 * 10 shares sold


def test_multiple_buys_entry_notional_uses_weighted_average_cost():
    prior = [make_buy(100.0, 10, decision_id=BUY_DECISION), make_buy(120.0, 10, decision_id=BUY_DECISION_2)]
    result = compute_realized_pnl(prior, sell_price=115.0, sell_qty=20, sell_decision_id=SELL_DECISION)
    assert result.entry_notional == 2200.0  # avg cost basis 110.0 * 20 shares sold


def test_partial_sell_entry_notional_scales_with_sell_qty_not_full_position():
    prior = [make_buy(100.0, 10)]
    result = compute_realized_pnl(prior, sell_price=110.0, sell_qty=4, sell_decision_id=SELL_DECISION)
    assert result.entry_notional == 400.0  # avg cost basis 100.0 * 4 shares sold (not all 10)


def test_partial_sell_still_uses_full_period_average_and_position_stays_open():
    prior = [make_buy(100.0, 10)]
    result = compute_realized_pnl(prior, sell_price=110.0, sell_qty=4, sell_decision_id=SELL_DECISION)
    assert result.pnl_amount == 40.0  # (110 - 100) * 4


def test_holding_period_resets_after_full_exit_and_rebuy():
    prior = [
        make_buy(100.0, 10, decision_id=BUY_DECISION),   # opens position
        make_sell(200.0, 10, decision_id=uuid.uuid4()),   # fully closes it — old buy no longer counts
        make_buy(50.0, 5, decision_id=BUY_DECISION_2),    # new holding period starts here
    ]
    result = compute_realized_pnl(prior, sell_price=60.0, sell_qty=5, sell_decision_id=SELL_DECISION)
    assert result.pnl_amount == 50.0  # (60 - 50) * 5, NOT blended with the old 100.0 buy
    assert set(result.decision_ids) == {BUY_DECISION_2, SELL_DECISION}


def test_multiple_partial_sells_within_same_period_both_use_same_average():
    prior = [
        make_buy(100.0, 20, decision_id=BUY_DECISION),
        make_sell(150.0, 5, decision_id=uuid.uuid4()),  # first partial sell, leaves 15 open
    ]
    result = compute_realized_pnl(prior, sell_price=120.0, sell_qty=5, sell_decision_id=SELL_DECISION)
    assert result.pnl_amount == 100.0  # (120 - 100) * 5 — same avg cost basis as the first partial sell
