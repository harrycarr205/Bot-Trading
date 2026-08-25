import datetime

from tradingsystem.db.models import AgentRun, Decision

NOW = datetime.datetime(2026, 1, 5, 14, 30)


def test_decisions_list_empty_state_does_not_500(client):
    response = client.get("/decisions")

    assert response.status_code == 200
    assert "No agent runs recorded yet." in response.text


def test_decisions_list_shows_runs(client, db_session):
    run = AgentRun(
        ticker="AAPL", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="decision_recorded",
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(Decision(agent_run_id=run.id, rating="Buy", decision="buy", reasoning_summary="test reasoning"))
    db_session.flush()

    response = client.get("/decisions")

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "Buy" in response.text


def test_decisions_list_filters_by_ticker(client, db_session):
    db_session.add(AgentRun(
        ticker="AAPL", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="market_closed",
    ))
    db_session.add(AgentRun(
        ticker="MSFT", run_type="pre_market", started_at=NOW, finished_at=NOW,
        market_status="open", outcome="market_closed",
    ))
    db_session.flush()

    response = client.get("/decisions?ticker=AAPL")

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "MSFT" not in response.text
