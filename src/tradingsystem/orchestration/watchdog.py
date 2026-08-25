"""Active heartbeat-failure alerting — ARCHITECTURE.md §7.

Nothing inside scheduler.py can alert on its own death: a dead or hung
process can't run any of its own code. This is a second, independent
process that periodically checks whether a scheduled cycle has fired when
the cron schedule says it should have, and alerts via Discord if not.

Entry point: python -m tradingsystem.orchestration.watchdog — run this
alongside scheduler.py, not instead of it.
"""

from __future__ import annotations

import datetime
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from tradingsystem.config import Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.orchestration import discord_alerts, heartbeat

log = logging.getLogger(__name__)

_TIMEZONE = "America/New_York"
_LOOKBACK = datetime.timedelta(days=8)


def most_recent_fire_time(trigger: CronTrigger, now: datetime.datetime) -> datetime.datetime | None:
    """The latest time this cron trigger was scheduled to fire at or before `now`.

    CronTrigger only exposes get_next_fire_time (no reverse lookup), so this
    walks forward from a lookback anchor, keeping the last fire time that
    does not exceed `now`.
    """
    candidate = trigger.get_next_fire_time(None, now - _LOOKBACK)
    last = None
    while candidate is not None and candidate <= now:
        last = candidate
        candidate = trigger.get_next_fire_time(candidate, candidate)
    return last


def check_heartbeat_staleness(session: Session, settings: Settings, now: datetime.datetime | None = None) -> None:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    pre_market = CronTrigger.from_crontab(settings.pre_market_cron, timezone=_TIMEZONE)
    midday = CronTrigger.from_crontab(settings.midday_cron, timezone=_TIMEZONE)
    expected_fires = [t for t in (most_recent_fire_time(pre_market, now), most_recent_fire_time(midday, now)) if t]
    if not expected_fires:
        return  # nothing has ever been scheduled to fire yet

    deadline = max(expected_fires) + datetime.timedelta(minutes=settings.heartbeat_grace_minutes)
    if now < deadline:
        return  # not overdue yet

    row = heartbeat.get_heartbeat(session)
    last_seen_at = row.last_seen_at if row is not None else None
    is_stale = last_seen_at is None or last_seen_at.replace(tzinfo=datetime.timezone.utc) < deadline
    if not is_stale:
        return

    already_alerted = row is not None and row.last_stale_alert_for == last_seen_at
    if already_alerted:
        return

    if last_seen_at is None:
        message = "HEARTBEAT ALERT: no scheduler heartbeat has ever been recorded, but a cycle was expected."
    else:
        message = (
            f"HEARTBEAT ALERT: scheduler last seen at {last_seen_at.isoformat()} UTC, "
            f"but a cycle was expected by {deadline.isoformat()}. The scheduler process may have died or hung."
        )
    discord_alerts.send_alert(settings, message, level="critical")

    if row is not None:
        row.last_stale_alert_for = last_seen_at
        session.commit()


def build_watchdog(settings: Settings | None = None) -> BlockingScheduler:
    settings = settings or Settings()
    scheduler = BlockingScheduler(timezone=_TIMEZONE)

    def _run() -> None:
        session = make_session_factory()()
        try:
            check_heartbeat_staleness(session, settings)
        except Exception:
            log.critical("check_heartbeat_staleness raised unhandled — watchdog continuing", exc_info=True)
            session.rollback()
        finally:
            session.close()

    scheduler.add_job(
        _run, IntervalTrigger(minutes=settings.watchdog_check_interval_minutes), id="heartbeat_watchdog",
    )
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_watchdog().start()
