import datetime
import zoneinfo

from apscheduler.triggers.cron import CronTrigger

from tradingsystem.config import Settings
from tradingsystem.db.models import SchedulerHeartbeat
from tradingsystem.orchestration import watchdog

NY = zoneinfo.ZoneInfo("America/New_York")

SETTINGS = Settings(
    pre_market_cron="35 9 * * mon-fri",
    midday_cron="30 12 * * mon-fri",
    heartbeat_grace_minutes=30,
)


def test_most_recent_fire_time_on_a_weekday():
    trigger = CronTrigger.from_crontab("35 9 * * mon-fri", timezone=NY)
    now = datetime.datetime(2026, 8, 31, 9, 40, tzinfo=NY)  # Monday, just after fire
    assert watchdog.most_recent_fire_time(trigger, now) == datetime.datetime(2026, 8, 31, 9, 35, tzinfo=NY)


def test_most_recent_fire_time_holds_across_a_weekend_gap():
    trigger = CronTrigger.from_crontab("30 12 * * mon-fri", timezone=NY)
    sunday = datetime.datetime(2026, 8, 30, 15, 0, tzinfo=NY)
    assert watchdog.most_recent_fire_time(trigger, sunday) == datetime.datetime(2026, 8, 28, 12, 30, tzinfo=NY)


def test_not_overdue_within_grace_period_no_alert(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(watchdog.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    now = datetime.datetime(2026, 8, 31, 9, 50, tzinfo=NY)  # 15 min after fire, within 30 min grace

    watchdog.check_heartbeat_staleness(db_session, SETTINGS, now=now)

    assert alerts == []


def test_overdue_with_never_recorded_heartbeat_alerts(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(watchdog.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    now = datetime.datetime(2026, 8, 31, 10, 30, tzinfo=NY)  # past 9:35 + 30 min grace

    watchdog.check_heartbeat_staleness(db_session, SETTINGS, now=now)

    assert len(alerts) == 1
    assert alerts[0][0] == "critical"
    assert "no scheduler heartbeat has ever been recorded" in alerts[0][1]


def test_overdue_with_stale_heartbeat_alerts_and_persists_marker(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(watchdog.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    stale_seen_at = datetime.datetime(2026, 8, 28, 12, 31)  # Friday midday, long before Monday's deadline
    db_session.add(SchedulerHeartbeat(component="scheduler", last_seen_at=stale_seen_at, last_run_type="midday", last_ticker="MSFT"))
    db_session.flush()
    now = datetime.datetime(2026, 8, 31, 10, 30, tzinfo=NY)

    watchdog.check_heartbeat_staleness(db_session, SETTINGS, now=now)

    assert len(alerts) == 1
    assert "scheduler last seen at" in alerts[0][1]
    row = db_session.query(SchedulerHeartbeat).filter_by(component="scheduler").one()
    assert row.last_stale_alert_for == stale_seen_at


def test_overdue_but_already_alerted_does_not_repeat(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(watchdog.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    stale_seen_at = datetime.datetime(2026, 8, 28, 12, 31)
    db_session.add(SchedulerHeartbeat(
        component="scheduler", last_seen_at=stale_seen_at, last_run_type="midday", last_ticker="MSFT",
        last_stale_alert_for=stale_seen_at,
    ))
    db_session.flush()
    now = datetime.datetime(2026, 8, 31, 10, 30, tzinfo=NY)

    watchdog.check_heartbeat_staleness(db_session, SETTINGS, now=now)

    assert alerts == []


def test_no_alert_when_heartbeat_is_actually_recent(db_session, monkeypatch):
    alerts = []
    monkeypatch.setattr(watchdog.discord_alerts, "send_alert", lambda settings, message, level="info": alerts.append((level, message)))
    now = datetime.datetime(2026, 8, 31, 10, 30, tzinfo=NY)
    recent_seen_at = datetime.datetime(2026, 8, 31, 14, 30)  # after the deadline, in UTC — cycle ran fine
    db_session.add(SchedulerHeartbeat(component="scheduler", last_seen_at=recent_seen_at, last_run_type="pre_market", last_ticker="AAPL"))
    db_session.flush()

    watchdog.check_heartbeat_staleness(db_session, SETTINGS, now=now)

    assert alerts == []
