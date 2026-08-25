import datetime
import uuid

from tradingsystem.db.models import AgentRun, DebateTranscript, Decision

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def test_decision_detail_404_for_missing_run(client):
    response = client.get(f"/decisions/{uuid.uuid4()}")

    assert response.status_code == 404


def test_decision_detail_shows_decision_and_transcripts_in_role_order(client, db_session):
    run = AgentRun(
        ticker="AAPL", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="decision_recorded",
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="strong fundamentals"))
    db_session.add(DebateTranscript(agent_run_id=run.id, role="trader", content="trader says buy"))
    db_session.add(DebateTranscript(agent_run_id=run.id, role="market_analyst", content="market looks strong"))
    db_session.flush()

    response = client.get(f"/decisions/{run.id}")

    assert response.status_code == 200
    body = response.text
    assert "strong fundamentals" in body
    assert body.index("market looks strong") < body.index("trader says buy")
