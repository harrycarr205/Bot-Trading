from tradingagents.agents.utils.memory import TradingMemoryLog

from tradingsystem.config import Settings
from tradingsystem.db.models import TradingMemoryEntry
from tradingsystem.orchestration.memory_ingestion import filter_new_entries, ingest_trading_memory


def make_entry(date="2026-08-20", ticker="AAPL"):
    return {"date": date, "ticker": ticker, "rating": "Buy", "pending": False,
            "raw": "+5.0%", "alpha": "+3.0%", "holding": "1d",
            "decision": "decision text", "reflection": "reflection text"}


def test_filter_new_entries_excludes_already_ingested():
    entries = [make_entry("2026-08-20", "AAPL"), make_entry("2026-08-21", "AAPL")]
    already = {"2026-08-20|AAPL"}
    result = filter_new_entries(entries, already)
    assert [e["date"] for e in result] == ["2026-08-21"]


def test_filter_new_entries_returns_all_when_none_ingested():
    entries = [make_entry("2026-08-20", "AAPL")]
    assert filter_new_entries(entries, set()) == entries


def test_ingest_trading_memory_ingests_resolved_and_skips_pending(db_session, tmp_path):
    log_path = tmp_path / "trading_memory.md"
    log = TradingMemoryLog({"memory_log_path": str(log_path)})
    log.store_decision(ticker="AAPL", trade_date="2026-08-20", final_trade_decision="**Rating**: Buy\nStrong fundamentals.")
    log.update_with_outcome(
        ticker="AAPL", trade_date="2026-08-20",
        raw_return=0.05, alpha_return=0.03, holding_days=1, reflection="Good call, held through volatility.",
    )
    log.store_decision(ticker="MSFT", trade_date="2026-08-21", final_trade_decision="**Rating**: Hold\nNo clear edge.")

    settings = Settings(tradingagents_memory_log_path=str(log_path))
    count = ingest_trading_memory(db_session, settings)

    assert count == 1
    entries = db_session.query(TradingMemoryEntry).all()
    assert len(entries) == 1
    assert entries[0].ticker == "AAPL"
    assert entries[0].source == "tradingagents_memory"
    assert entries[0].source_key == "2026-08-20|AAPL"
    assert "Good call, held through volatility." in entries[0].content
    assert "Strong fundamentals." in entries[0].content


def test_ingest_trading_memory_is_idempotent(db_session, tmp_path):
    log_path = tmp_path / "trading_memory.md"
    log = TradingMemoryLog({"memory_log_path": str(log_path)})
    log.store_decision(ticker="AAPL", trade_date="2026-08-20", final_trade_decision="**Rating**: Buy\nStrong fundamentals.")
    log.update_with_outcome(
        ticker="AAPL", trade_date="2026-08-20",
        raw_return=0.05, alpha_return=0.03, holding_days=1, reflection="Good call.",
    )
    settings = Settings(tradingagents_memory_log_path=str(log_path))

    first = ingest_trading_memory(db_session, settings)
    second = ingest_trading_memory(db_session, settings)

    assert first == 1
    assert second == 0
    assert db_session.query(TradingMemoryEntry).count() == 1
