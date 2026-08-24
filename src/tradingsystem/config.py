"""Settings loaded from .env (via pydantic-settings) plus the two YAML config files.

config/watchlist.yaml and config/risk_config.yaml are intentionally kept as separate,
freely editable files rather than env vars — see ARCHITECTURE.md §4/§5 notes on why
the watchlist and risk numbers are config, not code.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    trading_mode: str = "paper"

    alpaca_paper_api_key: str = ""
    alpaca_paper_api_secret: str = ""
    alpaca_paper_base_url: str = "https://paper-api.alpaca.markets"

    alpaca_live_api_key: str = ""
    alpaca_live_api_secret: str = ""
    alpaca_live_base_url: str = "https://api.alpaca.markets"

    database_url: str = "postgresql+psycopg://trading:trading@localhost:5432/trading"

    ollama_base_url: str = "http://localhost:11434"
    tradingagents_model: str = "qwen2.5:7b-instruct"

    discord_webhook_url: str = ""

    kill_switch_file: str = str(REPO_ROOT / "KILL_SWITCH")

    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8787


class RiskConfig(BaseModel):
    max_position_pct: float
    cash_reserve_pct: float
    stop_loss_pct: float
    daily_drawdown_breaker_pct: float
    weekly_drawdown_breaker_pct: float
    stale_data_max_age_minutes: int


class WatchlistConfig(BaseModel):
    tickers: list[str]


def load_risk_config(path: Path = REPO_ROOT / "config" / "risk_config.yaml") -> RiskConfig:
    data = yaml.safe_load(path.read_text())
    return RiskConfig.model_validate(data)


def load_watchlist(path: Path = REPO_ROOT / "config" / "watchlist.yaml") -> WatchlistConfig:
    data = yaml.safe_load(path.read_text())
    return WatchlistConfig.model_validate(data)
