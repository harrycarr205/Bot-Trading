"""GET/POST /api/config/* — candidate universe, risk config, env settings editing."""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tradingsystem.config import REPO_ROOT
from tradingsystem.dashboard import config_editing
from tradingsystem.dashboard.dependencies import require_same_origin
from tradingsystem.dashboard.schemas import ConfigResponse, RiskConfigOut
from tradingsystem.orchestration import process_control

router = APIRouter()

_RISK_CONFIG_PATH = REPO_ROOT / "config" / "risk_config.yaml"
_CANDIDATE_UNIVERSE_PATH = REPO_ROOT / "config" / "candidate_universe.yaml"
_ENV_PATH = REPO_ROOT / ".env"
_ENV_FIELDS = (
    "discovery_slots_per_cycle", "watchdog_check_interval_minutes",
    "pre_market_cron", "midday_cron",
    "tradingagents_deep_think_model", "tradingagents_quick_think_model",
)


@router.get("/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    env_values = {field: config_editing.read_env_value(_ENV_PATH, field.upper()) for field in _ENV_FIELDS}
    return ConfigResponse(
        tickers=config_editing.read_candidate_universe_tickers(_CANDIDATE_UNIVERSE_PATH),
        risk_config=RiskConfigOut(**config_editing.read_risk_config_values(_RISK_CONFIG_PATH)),
        env_values=env_values,
    )


class CandidateUniverseIn(BaseModel):
    tickers: list[str]


@router.post("/config/candidate-universe")
def post_candidate_universe(body: CandidateUniverseIn, _: None = Depends(require_same_origin)) -> dict:
    try:
        config_editing.write_candidate_universe(_CANDIDATE_UNIVERSE_PATH, body.tickers)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"saved": True}


class RiskConfigIn(BaseModel):
    max_position_pct: float
    cash_reserve_pct: float
    stop_loss_pct: float
    daily_drawdown_breaker_pct: float
    weekly_drawdown_breaker_pct: float
    stale_data_max_age_minutes: int
    note: str


@router.post("/config/risk-config")
def post_risk_config(body: RiskConfigIn, _: None = Depends(require_same_origin)) -> dict:
    note = body.note.strip()
    if not note:
        raise HTTPException(status_code=422, detail="justification is required")
    if len(note) > 2000:
        raise HTTPException(status_code=422, detail="justification must be 2000 characters or fewer")
    note = " ".join(note.split()).replace('"', "'")

    updates = body.model_dump(exclude={"note"})
    try:
        changes = config_editing.write_risk_config(_RISK_CONFIG_PATH, updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if changes:
        config_editing.append_config_change_log(process_control.RUN_DIR, changes, note)
    return {"saved": True}


class EnvSettingsIn(BaseModel):
    discovery_slots_per_cycle: int
    watchdog_check_interval_minutes: int
    pre_market_cron: str
    midday_cron: str
    tradingagents_deep_think_model: str
    tradingagents_quick_think_model: str


@router.post("/config/env-settings")
def post_env_settings(body: EnvSettingsIn, _: None = Depends(require_same_origin)) -> dict:
    if body.discovery_slots_per_cycle < 0:
        raise HTTPException(status_code=422, detail="discovery_slots_per_cycle must be >= 0")
    if body.watchdog_check_interval_minutes <= 0:
        raise HTTPException(status_code=422, detail="watchdog_check_interval_minutes must be > 0")
    for cron_value, field in ((body.pre_market_cron, "pre_market_cron"), (body.midday_cron, "midday_cron")):
        try:
            CronTrigger.from_crontab(cron_value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid {field}: {exc}") from exc
    if not body.tradingagents_deep_think_model.strip() or not body.tradingagents_quick_think_model.strip():
        raise HTTPException(status_code=422, detail="model names must not be empty")

    config_editing.write_env_values(_ENV_PATH, {
        "DISCOVERY_SLOTS_PER_CYCLE": str(body.discovery_slots_per_cycle),
        "WATCHDOG_CHECK_INTERVAL_MINUTES": str(body.watchdog_check_interval_minutes),
        "PRE_MARKET_CRON": body.pre_market_cron,
        "MIDDAY_CRON": body.midday_cron,
        "TRADINGAGENTS_DEEP_THINK_MODEL": body.tradingagents_deep_think_model,
        "TRADINGAGENTS_QUICK_THINK_MODEL": body.tradingagents_quick_think_model,
    })
    return {"saved": True}
