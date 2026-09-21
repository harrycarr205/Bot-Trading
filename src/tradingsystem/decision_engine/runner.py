"""Runs TradingAgents against local Ollama for one ticker, with retry and fail-closed
handling — ARCHITECTURE.md §5's non-negotiable: every structured output from the local
model is untrusted, schema-validated, retried on malformed output, fails closed (no
trade / no Decision row) on persistent failure.

Does not check market status itself, and does not call risk validation or execution —
that composition (market-status-first gate, size_order, validate_order, place_order)
belongs to the orchestration milestone. This module's job stops at: run the pipeline,
validate the rating, persist the full debate transcript + decision.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import uuid

from sqlalchemy.orm import Session
from tradingagents.graph.trading_graph import TradingAgentsGraph

from tradingsystem.config import RiskConfig, Settings
from tradingsystem.db.models import AgentRun, DebateTranscript, Decision
from tradingsystem.decision_engine.friction_context import build_friction_note, wrap_resolve_instrument_context
from tradingsystem.decision_engine.ta_config import build_ta_config
from tradingsystem.decision_engine.tool_audit import ToolCallAuditCallback
from tradingsystem.execution.alpaca_client import AlpacaClientProtocol, compute_average_daily_dollar_volume

log = logging.getLogger(__name__)

RATING_VALUES = {"Buy", "Overweight", "Hold", "Underweight", "Sell"}

_COLLAPSE = {
    "Buy": "buy",
    "Overweight": "buy",
    "Hold": "hold",
    "Underweight": "sell",
    "Sell": "sell",
}


@dataclasses.dataclass(frozen=True)
class ResearchResult:
    ok: bool
    agent_run_id: uuid.UUID
    decision_id: uuid.UUID | None = None
    rating: str | None = None
    decision: str | None = None
    reasoning_summary: str | None = None


def _persist_debate_transcript(session: Session, agent_run_id: uuid.UUID, final_state: dict) -> None:
    debate = final_state["investment_debate_state"]
    risk_debate = final_state["risk_debate_state"]
    rows = [
        ("market_analyst", final_state["market_report"]),
        ("sentiment_analyst", final_state["sentiment_report"]),
        ("news_analyst", final_state["news_report"]),
        ("fundamentals_analyst", final_state["fundamentals_report"]),
        ("bull_researcher", debate["bull_history"]),
        ("bear_researcher", debate["bear_history"]),
        ("research_manager_judge", debate["judge_decision"]),
        ("trader", final_state["trader_investment_plan"]),
        ("risk_aggressive", risk_debate["aggressive_history"]),
        ("risk_conservative", risk_debate["conservative_history"]),
        ("risk_neutral", risk_debate["neutral_history"]),
        ("risk_judge", risk_debate["judge_decision"]),
        ("investment_plan", final_state["investment_plan"]),
        ("portfolio_manager_final_decision", final_state["final_trade_decision"]),
    ]
    for role, content in rows:
        session.add(DebateTranscript(agent_run_id=agent_run_id, role=role, content=content or ""))


def run_research(
    session: Session,
    ticker: str,
    trade_date: str,
    market_status: str,
    alpaca_client: AlpacaClientProtocol,
    risk_config: RiskConfig,
    run_type: str = "pre_market",
    settings: Settings | None = None,
) -> ResearchResult:
    settings = settings or Settings()
    now = datetime.datetime.utcnow()

    agent_run = AgentRun(
        ticker=ticker,
        run_type=run_type,
        started_at=now,
        finished_at=None,
        market_status=market_status,
        outcome=None,
    )
    session.add(agent_run)
    session.flush()

    config = build_ta_config(settings)

    adv_bars = alpaca_client.get_recent_daily_bars([ticker], lookback_days=21)
    adv_notional = compute_average_daily_dollar_volume(adv_bars[ticker]) if ticker in adv_bars else None
    friction_note = build_friction_note(
        round_trip_cost_bps=settings.estimated_round_trip_cost_bps,
        adv_notional=adv_notional,
        max_pct_of_adv=risk_config.max_pct_of_adv,
    )

    last_error: Exception | None = None
    for attempt in range(1, settings.tradingagents_run_max_attempts + 1):
        tool_audit = ToolCallAuditCallback()
        try:
            graph = TradingAgentsGraph(debug=False, config=config, callbacks=[tool_audit])
            if hasattr(graph, "resolve_instrument_context"):
                # Test doubles (ScriptedGraph) don't implement this method —
                # only the real TradingAgentsGraph and fakes that opt in do.
                graph.resolve_instrument_context = wrap_resolve_instrument_context(
                    graph.resolve_instrument_context, friction_note,
                )
            final_state, rating = graph.propagate(ticker, trade_date)
        except Exception as exc:  # noqa: BLE001 - graph/langchain failures aren't consistently typed
            last_error = exc
            log.warning("TradingAgents run failed for %s (attempt %d): %s", ticker, attempt, exc)
            continue

        if rating not in RATING_VALUES:
            last_error = ValueError(f"unexpected rating {rating!r}, not in {RATING_VALUES}")
            log.warning(
                "TradingAgents returned an unrecognized rating for %s (attempt %d): %r",
                ticker, attempt, rating,
            )
            continue

        log.info(
            "tool calls requested for %s (agent_run=%s): %s",
            ticker, agent_run.id, sorted(set(tool_audit.requested_tool_names)),
        )

        agent_run.finished_at = datetime.datetime.utcnow()
        agent_run.outcome = "decision_recorded"
        _persist_debate_transcript(session, agent_run.id, final_state)
        decision = Decision(
            agent_run_id=agent_run.id,
            rating=rating,
            decision=_COLLAPSE[rating],
            reasoning_summary=final_state["final_trade_decision"],
        )
        session.add(decision)
        session.flush()

        return ResearchResult(
            ok=True,
            agent_run_id=agent_run.id,
            decision_id=decision.id,
            rating=rating,
            decision=decision.decision,
            reasoning_summary=decision.reasoning_summary,
        )

    agent_run.finished_at = datetime.datetime.utcnow()
    agent_run.outcome = "research_failed"
    session.flush()
    log.error(
        "TradingAgents research failed closed for %s after %d attempts: %s",
        ticker, settings.tradingagents_run_max_attempts, last_error,
    )
    return ResearchResult(ok=False, agent_run_id=agent_run.id)
