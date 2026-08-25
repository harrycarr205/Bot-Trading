from tradingsystem.risk.stop_loss import is_stop_loss_triggered


def test_triggered_when_drawdown_exceeds_threshold():
    assert is_stop_loss_triggered(entry_price=100.0, current_price=91.0, stop_loss_pct=0.08)


def test_not_triggered_when_drawdown_below_threshold():
    assert not is_stop_loss_triggered(entry_price=100.0, current_price=93.0, stop_loss_pct=0.08)


def test_not_triggered_at_exact_threshold():
    # 8.0% drawdown exactly at an 8% threshold — strictly greater-than, so this does not trigger.
    assert not is_stop_loss_triggered(entry_price=100.0, current_price=92.0, stop_loss_pct=0.08)


def test_triggered_just_past_threshold():
    assert is_stop_loss_triggered(entry_price=100.0, current_price=91.99, stop_loss_pct=0.08)


def test_not_triggered_when_price_is_up():
    assert not is_stop_loss_triggered(entry_price=100.0, current_price=110.0, stop_loss_pct=0.08)


def test_zero_entry_price_fails_closed_to_not_triggered():
    assert not is_stop_loss_triggered(entry_price=0.0, current_price=50.0, stop_loss_pct=0.08)


def test_negative_entry_price_fails_closed_to_not_triggered():
    assert not is_stop_loss_triggered(entry_price=-10.0, current_price=50.0, stop_loss_pct=0.08)
