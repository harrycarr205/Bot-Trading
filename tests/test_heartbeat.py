import datetime

from tradingsystem.orchestration.heartbeat import get_heartbeat, record_heartbeat


def test_get_heartbeat_returns_none_when_never_recorded(db_session):
    assert get_heartbeat(db_session) is None


def test_record_heartbeat_creates_row(db_session):
    before = datetime.datetime.utcnow()
    row = record_heartbeat(db_session, "pre_market", ticker="AAPL")

    assert row.component == "scheduler"
    assert row.last_run_type == "pre_market"
    assert row.last_ticker == "AAPL"
    assert row.last_seen_at >= before

    fetched = get_heartbeat(db_session)
    assert fetched is not None
    assert fetched.last_ticker == "AAPL"


def test_record_heartbeat_upserts_same_row(db_session):
    first = record_heartbeat(db_session, "pre_market", ticker="AAPL")
    second = record_heartbeat(db_session, "midday", ticker="MSFT")

    assert first.component == second.component == "scheduler"
    assert get_heartbeat(db_session).last_run_type == "midday"
    assert get_heartbeat(db_session).last_ticker == "MSFT"
