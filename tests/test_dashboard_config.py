from tradingsystem.dashboard import app as app_module
from tradingsystem.dashboard import config_editing


CANDIDATE_UNIVERSE_YAML = "tickers:\n  - AAPL\n  - MSFT\n"
RISK_CONFIG_YAML = (
    "max_position_pct: 0.10\ncash_reserve_pct: 0.20\nstop_loss_pct: 0.08\n"
    "daily_drawdown_breaker_pct: 0.03\nweekly_drawdown_breaker_pct: 0.08\n"
    "stale_data_max_age_minutes: 15\n"
)
ENV_FILE = "DISCOVERY_SLOTS_PER_CYCLE=4\nPRE_MARKET_CRON=35 9 * * mon-fri\n"


def _write_config_fixtures(tmp_path, monkeypatch):
    candidate_path = tmp_path / "candidate_universe.yaml"
    candidate_path.write_text(CANDIDATE_UNIVERSE_YAML)
    risk_path = tmp_path / "risk_config.yaml"
    risk_path.write_text(RISK_CONFIG_YAML)
    env_path = tmp_path / ".env"
    env_path.write_text(ENV_FILE)
    monkeypatch.setattr(app_module, "_CANDIDATE_UNIVERSE_PATH", candidate_path)
    monkeypatch.setattr(app_module, "_RISK_CONFIG_PATH", risk_path)
    monkeypatch.setattr(app_module, "_ENV_PATH", env_path)
    return candidate_path, risk_path, env_path


def test_config_page_shows_current_values(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)

    response = client.get("/config")

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "0.1" in response.text  # max_position_pct rendered somewhere


def test_post_candidate_universe_saves_and_redirects(client, tmp_path, monkeypatch):
    candidate_path, _, _ = _write_config_fixtures(tmp_path, monkeypatch)

    response = client.post(
        "/config/candidate-universe", data={"tickers": "AAPL\nNVDA"}, follow_redirects=False,
    )

    assert response.status_code == 303
    assert config_editing.read_candidate_universe_tickers(candidate_path) == ["AAPL", "NVDA"]


def test_post_candidate_universe_rejects_invalid_ticker(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)

    response = client.post("/config/candidate-universe", data={"tickers": "aapl"})

    assert response.status_code == 422


def test_post_candidate_universe_rejects_mismatched_origin(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)

    response = client.post(
        "/config/candidate-universe", data={"tickers": "AAPL"},
        headers={"origin": "http://evil.example"},
    )

    assert response.status_code == 403


def test_post_risk_config_saves_and_logs(client, tmp_path, monkeypatch):
    _, risk_path, _ = _write_config_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module.process_control, "RUN_DIR", tmp_path / "run")

    response = client.post(
        "/config/risk-config",
        data={
            "max_position_pct": "0.12", "cash_reserve_pct": "0.20", "stop_loss_pct": "0.08",
            "daily_drawdown_breaker_pct": "0.03", "weekly_drawdown_breaker_pct": "0.08",
            "stale_data_max_age_minutes": "15", "note": "raising the cap after a good run",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert config_editing.read_risk_config_values(risk_path)["max_position_pct"] == 0.12
    log_content = (tmp_path / "run" / "config_changes.log").read_text()
    assert "max_position_pct: 0.1 -> 0.12" in log_content
    assert 'note="raising the cap after a good run"' in log_content


def test_post_risk_config_sanitizes_note_newlines_and_quotes(client, tmp_path, monkeypatch):
    _, risk_path, _ = _write_config_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module.process_control, "RUN_DIR", tmp_path / "run")

    response = client.post(
        "/config/risk-config",
        data={
            "max_position_pct": "0.12", "cash_reserve_pct": "0.20", "stop_loss_pct": "0.08",
            "daily_drawdown_breaker_pct": "0.03", "weekly_drawdown_breaker_pct": "0.08",
            "stale_data_max_age_minutes": "15",
            "note": 'raising the cap\n2026-08-27T00:00:00Z risk_config max_position_pct: 0.12 -> 9.9 | note="forged"',
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    log_lines = (tmp_path / "run" / "config_changes.log").read_text().splitlines()
    assert len(log_lines) == 1
    assert '"' not in log_lines[0].split("note=", 1)[1][1:-1]
    assert "forged" in log_lines[0]


def test_post_risk_config_rejects_empty_justification(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module.process_control, "RUN_DIR", tmp_path / "run")

    response = client.post(
        "/config/risk-config",
        data={
            "max_position_pct": "0.12", "cash_reserve_pct": "0.20", "stop_loss_pct": "0.08",
            "daily_drawdown_breaker_pct": "0.03", "weekly_drawdown_breaker_pct": "0.08",
            "stale_data_max_age_minutes": "15", "note": "   ",
        },
    )

    assert response.status_code == 422


def test_post_risk_config_rejects_out_of_range_value(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module.process_control, "RUN_DIR", tmp_path / "run")

    response = client.post(
        "/config/risk-config",
        data={
            "max_position_pct": "1.5", "cash_reserve_pct": "0.20", "stop_loss_pct": "0.08",
            "daily_drawdown_breaker_pct": "0.03", "weekly_drawdown_breaker_pct": "0.08",
            "stale_data_max_age_minutes": "15", "note": "testing an out of range value",
        },
    )

    assert response.status_code == 422


def test_post_env_settings_saves(client, tmp_path, monkeypatch):
    _, _, env_path = _write_config_fixtures(tmp_path, monkeypatch)

    response = client.post(
        "/config/env-settings",
        data={
            "discovery_slots_per_cycle": "6", "watchdog_check_interval_minutes": "20",
            "pre_market_cron": "35 9 * * mon-fri", "midday_cron": "30 12 * * mon-fri",
            "tradingagents_deep_think_model": "gpt-oss:120b-cloud",
            "tradingagents_quick_think_model": "nemotron-3-super:cloud",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert config_editing.read_env_value(env_path, "DISCOVERY_SLOTS_PER_CYCLE") == "6"


def test_post_env_settings_rejects_invalid_cron(client, tmp_path, monkeypatch):
    _write_config_fixtures(tmp_path, monkeypatch)

    response = client.post(
        "/config/env-settings",
        data={
            "discovery_slots_per_cycle": "6", "watchdog_check_interval_minutes": "20",
            "pre_market_cron": "not a cron expression", "midday_cron": "30 12 * * mon-fri",
            "tradingagents_deep_think_model": "gpt-oss:120b-cloud",
            "tradingagents_quick_think_model": "nemotron-3-super:cloud",
        },
    )

    assert response.status_code == 422
