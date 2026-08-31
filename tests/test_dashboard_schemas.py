import datetime
import uuid

from tradingsystem.dashboard.schemas import (
    BreakerOut, HeartbeatOut, OverviewResponse, OverviewSnapshot, PositionOut, PositionsResponse,
)


def test_overview_response_serializes_to_json_compatible_types():
    resp = OverviewResponse(
        snapshot=OverviewSnapshot(snapshot_date=datetime.date(2026, 8, 24), equity=100_000.0, cash=80_000.0),
        heartbeat=HeartbeatOut(
            last_seen_at=datetime.datetime(2026, 8, 24, 9, 0), last_run_type="pre_market", last_ticker="AAPL",
        ),
        heartbeat_stale=False,
        active_breakers=[BreakerOut(
            id=uuid.uuid4(), breaker_type="daily", trigger_reason="4% drawdown",
            tripped_at=datetime.datetime(2026, 8, 24, 9, 0),
        )],
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["snapshot"]["equity"] == 100_000.0
    assert dumped["active_breakers"][0]["breaker_type"] == "daily"


def test_position_out_computes_from_plain_fields():
    p = PositionOut(
        ticker="AAPL", qty=10.0, avg_entry_price=180.0, current_price=184.5,
        unrealized_pnl=45.0, latest_decision="buy", latest_rating="Buy",
    )
    assert PositionsResponse(positions=[p], candidates=[]).positions[0].ticker == "AAPL"
