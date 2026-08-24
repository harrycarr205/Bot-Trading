"""Single-row liveness marker for the orchestration scheduler — ARCHITECTURE.md §6/§7.

Updated every cycle regardless of trade activity, so a silent crash is distinguishable
from a legitimate no-trade day. A future dashboard/monitor reads staleness from this;
this module only writes it.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from tradingsystem.db.models import SchedulerHeartbeat

_COMPONENT = "scheduler"


def record_heartbeat(session: Session, run_type: str, ticker: str | None = None) -> SchedulerHeartbeat:
    now = datetime.datetime.utcnow()
    row = session.get(SchedulerHeartbeat, _COMPONENT)
    if row is None:
        row = SchedulerHeartbeat(component=_COMPONENT, last_seen_at=now, last_run_type=run_type, last_ticker=ticker)
        session.add(row)
    else:
        row.last_seen_at = now
        row.last_run_type = run_type
        row.last_ticker = ticker
    session.flush()
    return row


def get_heartbeat(session: Session) -> SchedulerHeartbeat | None:
    return session.get(SchedulerHeartbeat, _COMPONENT)
