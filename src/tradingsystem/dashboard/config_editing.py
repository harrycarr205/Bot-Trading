"""File-I/O layer for the dashboard's /config page — dashboard-config-editing-design.md.

No FastAPI dependency here, so every function is testable against a plain
tmp_path without a running app. ruamel.yaml's round-trip mode is used for
both YAML files specifically to preserve their header comments across an
edit — risk_config.yaml's comments state the file's own safety philosophy,
plain yaml.safe_dump would destroy them on the first save.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True

_TICKER_PATTERN = re.compile(r"^[A-Z.]{1,10}$")


def read_candidate_universe_tickers(path: Path) -> list[str]:
    with path.open("r") as f:
        data = _yaml.load(f)
    return list(data["tickers"])


def write_candidate_universe(path: Path, tickers: list[str]) -> None:
    if not tickers:
        raise ValueError("candidate universe must not be empty")
    for ticker in tickers:
        if not _TICKER_PATTERN.match(ticker):
            raise ValueError(f"invalid ticker {ticker!r}: must be uppercase letters/dots only")

    with path.open("r") as f:
        data = _yaml.load(f)
    data["tickers"] = tickers
    with path.open("w") as f:
        _yaml.dump(data, f)


_RISK_CONFIG_PCT_FIELDS = (
    "max_position_pct", "cash_reserve_pct", "stop_loss_pct",
    "daily_drawdown_breaker_pct", "weekly_drawdown_breaker_pct",
)


def read_risk_config_values(path: Path) -> dict[str, float | int]:
    with path.open("r") as f:
        data = _yaml.load(f)
    return {
        **{field: float(data[field]) for field in _RISK_CONFIG_PCT_FIELDS},
        "stale_data_max_age_minutes": int(data["stale_data_max_age_minutes"]),
    }


def _validate_risk_config_field(field: str, value: float | int) -> None:
    if field in _RISK_CONFIG_PCT_FIELDS:
        if not (0 < value <= 1):
            raise ValueError(f"{field} must be in (0, 1], got {value}")
    elif field == "stale_data_max_age_minutes":
        if value <= 0:
            raise ValueError(f"{field} must be > 0, got {value}")
    else:
        raise ValueError(f"unknown risk_config field: {field}")


def write_risk_config(
    path: Path, updates: dict[str, float | int],
) -> dict[str, tuple[float | int, float | int]]:
    for field, value in updates.items():
        _validate_risk_config_field(field, value)

    with path.open("r") as f:
        data = _yaml.load(f)

    changes: dict[str, tuple[float | int, float | int]] = {}
    for field, new_value in updates.items():
        old_value = data[field]
        if field in _RISK_CONFIG_PCT_FIELDS:
            old_value = float(old_value)
        else:
            old_value = int(old_value)
        if old_value != new_value:
            changes[field] = (old_value, new_value)
            data[field] = new_value

    with path.open("w") as f:
        _yaml.dump(data, f)
    return changes
