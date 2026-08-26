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
