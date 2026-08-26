"""Settings loaded from .env (via pydantic-settings) plus the two YAML config files.

config/candidate_universe.yaml and config/risk_config.yaml are intentionally kept as separate,
freely editable files rather than env vars — see ARCHITECTURE.md §4/§5 notes on why
the candidate universe and risk numbers are config, not code.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
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
    # Dedicated database for tests/conftest.py's db_session fixture — kept
    # separate from database_url so the test suite is never polluted by (or
    # accidentally pollutes) real data written by a live running scheduler.
    test_database_url: str = "postgresql+psycopg://trading:trading@localhost:5432/trading_test"

    # TradingAgents' Ollama provider uses this URL as-is (no auto-appended
    # /v1) when OLLAMA_BASE_URL is set — confirmed from the installed
    # package's provider table.
    ollama_base_url: str = "http://localhost:11434/v1"
    # Deep-think: Research Manager, Trader, Risk Judge, Portfolio Manager.
    # Quick-think: the four analysts. Independently configurable so a better
    # model (local or Ollama-Cloud, e.g. a "<name>:cloud" tag) can be swapped
    # in per role via .env with no code change.
    tradingagents_deep_think_model: str = "qwen2.5:7b-instruct"
    tradingagents_quick_think_model: str = "qwen2.5:7b-instruct"
    # ARCHITECTURE.md §5: shallow, single-round debate to validate the
    # pipeline end-to-end first. Not part of risk_config.yaml — that file is
    # specifically the risk *validation* numbers, not decision-engine depth.
    tradingagents_max_debate_rounds: int = 1
    tradingagents_max_risk_discuss_rounds: int = 1
    # Outer retry budget around a whole TradingAgentsGraph.propagate() call,
    # separate from TradingAgents' own internal SDK-level llm_max_retries —
    # covers the graph raising on unrecoverable malformed structured output.
    tradingagents_run_max_attempts: int = 2
    # Empty means "use TradingAgents' own default location" — set only to
    # override where its trading_memory.md reflection log lives, e.g. for
    # test isolation or relocating it into a shared/managed path.
    tradingagents_memory_log_path: str = ""

    # Orchestration schedule (ARCHITECTURE.md §6) — standard 5-field cron syntax,
    # always interpreted in America/New_York regardless of host machine locale.
    pre_market_cron: str = "35 9 * * mon-fri"
    midday_cron: str = "30 12 * * mon-fri"

    discord_webhook_url: str = ""

    # Watchdog (orchestration/watchdog.py) — a second standalone process that
    # alerts if the scheduler process dies or hangs, since nothing inside a
    # dead process can alert on its own death.
    heartbeat_grace_minutes: int = 30
    watchdog_check_interval_minutes: int = 20

    # Discovery slots for orchestration/ticker_selection.py's screen — held
    # positions are always reassessed on top of this, uncapped (see
    # docs/superpowers/specs/2026-08-25-ticker-selection-design.md).
    discovery_slots_per_cycle: int = Field(default=4, ge=0)

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


class CandidateUniverseConfig(BaseModel):
    tickers: list[str]


def load_risk_config(path: Path = REPO_ROOT / "config" / "risk_config.yaml") -> RiskConfig:
    data = yaml.safe_load(path.read_text())
    return RiskConfig.model_validate(data)


def load_candidate_universe(
    path: Path = REPO_ROOT / "config" / "candidate_universe.yaml",
) -> CandidateUniverseConfig:
    data = yaml.safe_load(path.read_text())
    return CandidateUniverseConfig.model_validate(data)
