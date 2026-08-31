import shutil

import pytest

from tradingsystem.config import REPO_ROOT


@pytest.fixture
def isolated_config_files(tmp_path):
    candidate_src = REPO_ROOT / "config" / "candidate_universe.yaml"
    risk_src = REPO_ROOT / "config" / "risk_config.yaml"
    candidate_copy = tmp_path / "candidate_universe.yaml"
    risk_copy = tmp_path / "risk_config.yaml"
    shutil.copy(candidate_src, candidate_copy)
    shutil.copy(risk_src, risk_copy)

    from tradingsystem.dashboard.routes import config as config_routes
    original_candidate, original_risk = config_routes._CANDIDATE_UNIVERSE_PATH, config_routes._RISK_CONFIG_PATH
    config_routes._CANDIDATE_UNIVERSE_PATH = candidate_copy
    config_routes._RISK_CONFIG_PATH = risk_copy
    yield
    config_routes._CANDIDATE_UNIVERSE_PATH = original_candidate
    config_routes._RISK_CONFIG_PATH = original_risk


def test_api_config_get_returns_tickers_and_risk_config(client, isolated_config_files):
    response = client.get("/api/config")

    body = response.json()
    assert isinstance(body["tickers"], list)
    assert "max_position_pct" in body["risk_config"]


def test_api_config_save_candidate_universe_updates_tickers(client, isolated_config_files):
    response = client.post("/api/config/candidate-universe", json={"tickers": ["AAPL", "MSFT"]})

    assert response.status_code == 200
    body = client.get("/api/config").json()
    assert body["tickers"] == ["AAPL", "MSFT"]


def test_api_config_risk_config_requires_note(client, isolated_config_files):
    response = client.post("/api/config/risk-config", json={
        "max_position_pct": 0.1, "cash_reserve_pct": 0.2, "stop_loss_pct": 0.08,
        "daily_drawdown_breaker_pct": 0.03, "weekly_drawdown_breaker_pct": 0.08,
        "stale_data_max_age_minutes": 15, "note": "",
    })

    assert response.status_code == 422
