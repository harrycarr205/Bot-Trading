"""Ingest TradingAgents' own trading_memory.md reflection log into the second
brain — ARCHITECTURE.md §3, rather than leaving it a separate disconnected file.

TradingMemoryLog (from the tradingagents package) writes/updates this file as
a side effect of every TradingAgentsGraph.propagate() call in runner.py — we
never write to it ourselves, only read it here.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from tradingagents.agents.utils.memory import TradingMemoryLog

from tradingsystem.config import Settings
from tradingsystem.db.models import TradingMemoryEntry
from tradingsystem.decision_engine.ta_config import build_ta_config


def _entry_key(entry: dict) -> str:
    return f"{entry['date']}|{entry['ticker']}"


def filter_new_entries(entries: list[dict], already_ingested_keys: set[str]) -> list[dict]:
    return [e for e in entries if _entry_key(e) not in already_ingested_keys]


def _format_content(entry: dict) -> str:
    return (
        f"[{entry['date']} | {entry['ticker']} | {entry['rating']} | "
        f"raw {entry['raw'] or 'n/a'} | alpha {entry['alpha'] or 'n/a'} | held {entry['holding'] or 'n/a'}]\n\n"
        f"DECISION:\n{entry['decision']}\n\nREFLECTION:\n{entry['reflection']}"
    )


def ingest_trading_memory(session: Session, settings: Settings | None = None) -> int:
    """Ingest every resolved (non-pending) entry not already ingested. Returns count."""
    settings = settings or Settings()
    memory_log = TradingMemoryLog(build_ta_config(settings))
    resolved_entries = [e for e in memory_log.load_entries() if not e.get("pending")]

    already_ingested = {
        key for (key,) in session.query(TradingMemoryEntry.source_key)
        .filter(TradingMemoryEntry.source_key.isnot(None))
        .all()
    }

    new_entries = filter_new_entries(resolved_entries, already_ingested)
    for entry in new_entries:
        session.add(TradingMemoryEntry(
            ticker=entry["ticker"],
            content=_format_content(entry),
            source="tradingagents_memory",
            source_key=_entry_key(entry),
        ))
    if new_entries:
        session.flush()
    return len(new_entries)
