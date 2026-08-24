import uuid

import pytest

from tradingsystem.config import Settings
from tradingsystem.db.models import AgentRun, DebateTranscript, Decision
from tradingsystem.decision_engine import runner as runner_module
from tradingsystem.decision_engine.runner import run_research


def make_final_state(final_trade_decision="Buy: strong fundamentals"):
    return {
        "market_report": "market report",
        "sentiment_report": "sentiment report",
        "news_report": "news report",
        "fundamentals_report": "fundamentals report",
        "investment_debate_state": {
            "bull_history": "bull says buy",
            "bear_history": "bear says wait",
            "judge_decision": "go with bull",
        },
        "trader_investment_plan": "trader plan",
        "risk_debate_state": {
            "aggressive_history": "aggressive take",
            "conservative_history": "conservative take",
            "neutral_history": "neutral take",
            "judge_decision": "risk judge decision",
        },
        "investment_plan": "investment plan",
        "final_trade_decision": final_trade_decision,
    }


class ScriptedGraph:
    """Stand-in for TradingAgentsGraph — scripted per-call outcomes, no real Ollama call."""

    calls = []

    def __init__(self, debug=False, config=None):
        pass

    def propagate(self, ticker, trade_date):
        outcome = ScriptedGraph.calls.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_successful_run_persists_agent_run_decision_and_transcript(db_session, monkeypatch):
    ScriptedGraph.calls = [(make_final_state(), "Buy")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)

    settings = Settings(tradingagents_run_max_attempts=2)
    result = run_research(db_session, "AAPL", "2026-01-05", market_status="open", settings=settings)

    assert result.ok
    assert result.rating == "Buy"
    assert result.decision == "buy"

    agent_run = db_session.query(AgentRun).filter_by(id=result.agent_run_id).one()
    assert agent_run.outcome == "decision_recorded"
    assert agent_run.finished_at is not None

    decision = db_session.query(Decision).filter_by(agent_run_id=agent_run.id).one()
    assert decision.rating == "Buy"
    assert decision.decision == "buy"

    transcripts = db_session.query(DebateTranscript).filter_by(agent_run_id=agent_run.id).all()
    roles = {t.role for t in transcripts}
    assert "market_analyst" in roles
    assert "portfolio_manager_final_decision" in roles
    assert len(transcripts) == 14

    assert result.decision_id == decision.id


def test_unrecognized_rating_retries_then_fails_closed(db_session, monkeypatch):
    ScriptedGraph.calls = [
        (make_final_state(), "Strong Buy"),  # not in RATING_VALUES
        (make_final_state(), "also not valid"),
    ]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)

    settings = Settings(tradingagents_run_max_attempts=2)
    result = run_research(db_session, "AAPL", "2026-01-05", market_status="open", settings=settings)

    assert not result.ok
    agent_run = db_session.query(AgentRun).filter_by(id=result.agent_run_id).one()
    assert agent_run.outcome == "research_failed"
    assert db_session.query(Decision).filter_by(agent_run_id=agent_run.id).count() == 0


def test_exception_every_attempt_fails_closed(db_session, monkeypatch):
    ScriptedGraph.calls = [RuntimeError("ollama connection refused"), RuntimeError("still down")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)

    settings = Settings(tradingagents_run_max_attempts=2)
    result = run_research(db_session, "AAPL", "2026-01-05", market_status="open", settings=settings)

    assert not result.ok
    agent_run = db_session.query(AgentRun).filter_by(id=result.agent_run_id).one()
    assert agent_run.outcome == "research_failed"


def test_succeeds_on_second_attempt_after_one_failure(db_session, monkeypatch):
    ScriptedGraph.calls = [RuntimeError("transient failure"), (make_final_state(), "Hold")]
    monkeypatch.setattr(runner_module, "TradingAgentsGraph", ScriptedGraph)

    settings = Settings(tradingagents_run_max_attempts=2)
    result = run_research(db_session, "AAPL", "2026-01-05", market_status="open", settings=settings)

    assert result.ok
    assert result.rating == "Hold"
    assert result.decision == "hold"
