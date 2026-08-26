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
