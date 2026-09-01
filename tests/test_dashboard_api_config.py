import shutil

import pytest

from tradingsystem.config import REPO_ROOT
from tradingsystem.orchestration import process_control


@pytest.fixture
def isolated_config_files(tmp_path, monkeypatch):
    candidate_src = REPO_ROOT / "config" / "candidate_universe.yaml"
    risk_src = REPO_ROOT / "config" / "risk_config.yaml"
    env_src = REPO_ROOT / ".env"
    candidate_copy = tmp_path / "candidate_universe.yaml"
    risk_copy = tmp_path / "risk_config.yaml"
    env_copy = tmp_path / ".env"
    shutil.copy(candidate_src, candidate_copy)
    shutil.copy(risk_src, risk_copy)
    shutil.copy(env_src, env_copy)

    from tradingsystem.dashboard.routes import config as config_routes
    monkeypatch.setattr(config_routes, "_CANDIDATE_UNIVERSE_PATH", candidate_copy)
    monkeypatch.setattr(config_routes, "_RISK_CONFIG_PATH", risk_copy)
    monkeypatch.setattr(config_routes, "_ENV_PATH", env_copy)

    # Isolate the run directory for audit log testing
    run_dir_copy = tmp_path / "run"
    monkeypatch.setattr(process_control, "RUN_DIR", run_dir_copy)

    yield tmp_path  # Yield tmp_path so tests can access files if needed


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


def test_api_config_save_risk_config_succeeds_and_appends_audit_log(client, isolated_config_files):
    # Get initial value
    initial_config = client.get("/api/config").json()
    initial_max_pct = initial_config["risk_config"]["max_position_pct"]

    # POST with a changed value and a note
    new_max_pct = 0.15
    note = "Testing audit log for risk config changes"
    response = client.post("/api/config/risk-config", json={
        "max_position_pct": new_max_pct,
        "cash_reserve_pct": initial_config["risk_config"]["cash_reserve_pct"],
        "stop_loss_pct": initial_config["risk_config"]["stop_loss_pct"],
        "daily_drawdown_breaker_pct": initial_config["risk_config"]["daily_drawdown_breaker_pct"],
        "weekly_drawdown_breaker_pct": initial_config["risk_config"]["weekly_drawdown_breaker_pct"],
        "stale_data_max_age_minutes": initial_config["risk_config"]["stale_data_max_age_minutes"],
        "note": note,
    })

    assert response.status_code == 200
    assert response.json() == {"saved": True}

    # Verify the change was applied
    updated_config = client.get("/api/config").json()
    assert updated_config["risk_config"]["max_position_pct"] == new_max_pct

    # Verify the audit log was appended with the sanitized note
    audit_log_path = isolated_config_files / "run" / "config_changes.log"
    assert audit_log_path.exists(), f"Audit log not found at {audit_log_path}"
    audit_log_content = audit_log_path.read_text()
    assert note in audit_log_content, f"Note '{note}' not found in audit log:\n{audit_log_content}"
    assert f"max_position_pct: {initial_max_pct} -> {new_max_pct}" in audit_log_content


def test_api_config_save_env_settings_succeeds(client, isolated_config_files):
    response = client.post("/api/config/env-settings", json={
        "discovery_slots_per_cycle": 5,
        "watchdog_check_interval_minutes": 2,
        "pre_market_cron": "0 9 * * 1-5",
        "midday_cron": "0 12 * * 1-5",
        "tradingagents_deep_think_model": "deep-model-v1",
        "tradingagents_quick_think_model": "quick-model-v1",
    })

    assert response.status_code == 200
    assert response.json() == {"saved": True}


def test_api_config_save_candidate_universe_rejects_invalid_ticker(client, isolated_config_files):
    response = client.post("/api/config/candidate-universe", json={"tickers": ["aapl"]})

    assert response.status_code == 422


def test_api_config_save_candidate_universe_rejects_mismatched_origin(client, isolated_config_files):
    candidate_path = isolated_config_files / "candidate_universe.yaml"
    before = candidate_path.read_text()

    response = client.post(
        "/api/config/candidate-universe", json={"tickers": ["AAPL"]},
        headers={"origin": "http://evil.example"},
    )

    assert response.status_code == 403
    assert candidate_path.read_text() == before


def test_api_config_risk_config_rejects_mismatched_origin(client, isolated_config_files):
    risk_path = isolated_config_files / "risk_config.yaml"
    before = risk_path.read_text()

    response = client.post(
        "/api/config/risk-config",
        json={
            "max_position_pct": 0.12, "cash_reserve_pct": 0.2, "stop_loss_pct": 0.08,
            "daily_drawdown_breaker_pct": 0.03, "weekly_drawdown_breaker_pct": 0.08,
            "stale_data_max_age_minutes": 15, "note": "testing origin rejection",
        },
        headers={"origin": "http://evil.example"},
    )

    assert response.status_code == 403
    assert risk_path.read_text() == before


def test_api_config_risk_config_sanitizes_note_newlines_and_quotes(client, isolated_config_files):
    response = client.post("/api/config/risk-config", json={
        "max_position_pct": 0.12, "cash_reserve_pct": 0.2, "stop_loss_pct": 0.08,
        "daily_drawdown_breaker_pct": 0.03, "weekly_drawdown_breaker_pct": 0.08,
        "stale_data_max_age_minutes": 15,
        "note": 'raising the cap\n2026-08-27T00:00:00Z risk_config max_position_pct: 0.12 -> 9.9 | note="forged"',
    })

    assert response.status_code == 200
    audit_log_path = isolated_config_files / "run" / "config_changes.log"
    log_lines = audit_log_path.read_text().splitlines()
    assert len(log_lines) == 1
    assert '"' not in log_lines[0].split("note=", 1)[1][1:-1]
    assert "forged" in log_lines[0]


def test_api_config_risk_config_rejects_out_of_range_value(client, isolated_config_files):
    response = client.post("/api/config/risk-config", json={
        "max_position_pct": 1.5, "cash_reserve_pct": 0.2, "stop_loss_pct": 0.08,
        "daily_drawdown_breaker_pct": 0.03, "weekly_drawdown_breaker_pct": 0.08,
        "stale_data_max_age_minutes": 15, "note": "testing an out of range value",
    })

    assert response.status_code == 422


def test_api_config_env_settings_rejects_invalid_cron(client, isolated_config_files):
    response = client.post("/api/config/env-settings", json={
        "discovery_slots_per_cycle": 6, "watchdog_check_interval_minutes": 20,
        "pre_market_cron": "not a cron expression", "midday_cron": "30 12 * * mon-fri",
        "tradingagents_deep_think_model": "gpt-oss:120b-cloud",
        "tradingagents_quick_think_model": "nemotron-3-super:cloud",
    })

    assert response.status_code == 422


def test_api_config_env_settings_rejects_mismatched_origin(client, isolated_config_files):
    env_path = isolated_config_files / ".env"
    before = env_path.read_text()

    response = client.post(
        "/api/config/env-settings",
        json={
            "discovery_slots_per_cycle": 6, "watchdog_check_interval_minutes": 20,
            "pre_market_cron": "0 9 * * 1-5", "midday_cron": "30 12 * * mon-fri",
            "tradingagents_deep_think_model": "gpt-oss:120b-cloud",
            "tradingagents_quick_think_model": "nemotron-3-super:cloud",
        },
        headers={"origin": "http://evil.example"},
    )

    assert response.status_code == 403
    assert env_path.read_text() == before
