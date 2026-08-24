"""Build a TradingAgents config dict from our own Settings.

ARCHITECTURE.md §5: same model for every agent role (deep + quick), Ollama-backed,
shallow single-round debate to validate the pipeline end-to-end first.
"""

from __future__ import annotations

from typing import Any

from tradingagents.default_config import DEFAULT_CONFIG

from tradingsystem.config import Settings


def build_ta_config(settings: Settings) -> dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "ollama"
    config["deep_think_llm"] = settings.tradingagents_model
    config["quick_think_llm"] = settings.tradingagents_model
    config["backend_url"] = settings.ollama_base_url
    config["max_debate_rounds"] = settings.tradingagents_max_debate_rounds
    config["max_risk_discuss_rounds"] = settings.tradingagents_max_risk_discuss_rounds
    return config
