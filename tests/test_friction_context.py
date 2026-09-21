from tradingsystem.decision_engine.friction_context import (
    build_friction_note,
    wrap_resolve_instrument_context,
)


def test_build_friction_note_includes_cost_bps():
    note = build_friction_note(round_trip_cost_bps=10.0, adv_notional=1_000_000.0, max_pct_of_adv=0.10)
    assert "10" in note
    assert "basis points" in note


def test_build_friction_note_includes_adv_threshold():
    note = build_friction_note(round_trip_cost_bps=10.0, adv_notional=1_000_000.0, max_pct_of_adv=0.10)
    assert "1,000,000" in note
    assert "10%" in note


def test_build_friction_note_handles_missing_adv_gracefully():
    note = build_friction_note(round_trip_cost_bps=10.0, adv_notional=None, max_pct_of_adv=0.10)
    assert "unavailable" in note
    assert "10" in note  # the cost sentence is still present


def test_wrap_resolve_instrument_context_appends_friction_note():
    def original(ticker, asset_type="stock"):
        return f"The instrument to analyze is `{ticker}`."

    wrapped = wrap_resolve_instrument_context(original, "FRICTION NOTE HERE")
    result = wrapped("AAPL", "stock")
    assert result.startswith("The instrument to analyze is `AAPL`.")
    assert result.endswith("FRICTION NOTE HERE")


def test_wrap_resolve_instrument_context_defaults_asset_type():
    def original(ticker, asset_type="stock"):
        return f"{ticker}/{asset_type}"

    wrapped = wrap_resolve_instrument_context(original, "note")
    assert wrapped("AAPL") == "AAPL/stock note"
