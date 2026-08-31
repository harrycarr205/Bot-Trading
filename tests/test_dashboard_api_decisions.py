import datetime
import uuid

from tradingsystem.db.models import AgentRun, Decision, DebateTranscript


def _make_run_with_decision(db_session, ticker="AAPL"):
    now = datetime.datetime.utcnow()
    run = AgentRun(ticker=ticker, run_type="pre_market", started_at=now, finished_at=now,
                    market_status="open", outcome="decision_recorded")
    db_session.add(run)
    db_session.flush()
    decision = Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="looks good")
    db_session.add(decision)
    db_session.flush()
    return run, decision


def test_api_decisions_lists_runs_newest_first(client, db_session):
    _make_run_with_decision(db_session, "AAPL")
    _make_run_with_decision(db_session, "MSFT")

    response = client.get("/api/decisions")

    body = response.json()
    assert len(body["runs"]) >= 2
    assert body["runs"][0]["decision"]["decision"] in ("buy", "sell", "hold")


def test_api_decisions_filters_by_ticker(client, db_session):
    _make_run_with_decision(db_session, "AAPL")
    _make_run_with_decision(db_session, "MSFT")

    response = client.get("/api/decisions", params={"ticker": "AAPL"})

    body = response.json()
    assert all(r["ticker"] == "AAPL" for r in body["runs"])


def test_api_decision_detail_orders_transcripts_by_role(client, db_session):
    run, decision = _make_run_with_decision(db_session)
    db_session.add(DebateTranscript(agent_run_id=run.id, role="trader", content="buy it"))
    db_session.add(DebateTranscript(agent_run_id=run.id, role="market_analyst", content="trending up"))
    db_session.flush()

    response = client.get(f"/api/decisions/{run.id}")

    body = response.json()
    roles = [t["role"] for t in body["transcripts"]]
    assert roles.index("market_analyst") < roles.index("trader")


def test_api_decision_detail_404_for_missing_run(client, db_session):
    response = client.get(f"/api/decisions/{uuid.uuid4()}")
    assert response.status_code == 404
