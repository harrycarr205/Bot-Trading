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
from ruamel.yaml.tokens import CommentToken

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
    existing = data["tickers"]
    existing_list = list(existing)
    to_remove_indices = [i for i, t in enumerate(existing_list) if t not in tickers]

    # ruamel attaches a standalone ("own line") comment to the *preceding*
    # item's slot, even though visually it reads as a header for whatever
    # ticker follows it (e.g. a "# Healthcare" section header sitting above
    # the first healthcare ticker gets attached to the last tech ticker
    # above it). A plain remove() would silently drop that header along with
    # the removed ticker. Walk removals back-to-front and, for any standalone
    # comment on a removed item, carry it onto the new predecessor (or onto
    # the sequence's own leading comment, if the removed item was first) so
    # section headers survive even when the ticker they used to trail behind
    # is the one being removed. An inline same-line comment (no leading
    # newline) genuinely belongs to the removed ticker itself and is dropped
    # with it, as intended.
    for idx in reversed(to_remove_indices):
        entry = existing.ca.items.get(idx)
        comment = entry[0] if entry else None
        if comment is not None and comment.value.startswith("\n"):
            if idx > 0:
                prev_entry = existing.ca.items.setdefault(idx - 1, [None, None, None, None])
                if prev_entry[0] is None:
                    prev_entry[0] = comment
                else:
                    prev_entry[0] = CommentToken(
                        prev_entry[0].value + comment.value, comment.start_mark, comment.end_mark,
                    )
            else:
                pre = existing.ca.comment
                lst = list(pre[1]) if pre is not None and pre[1] is not None else []
                lst.append(comment)
                existing.ca.comment = [pre[0] if pre is not None else None, lst]
        del existing[idx]

    for ticker in tickers:
        if ticker not in existing_list:
            existing.append(ticker)
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


def append_config_change_log(
    run_dir: Path, changes: dict[str, tuple[float | int, float | int]], note: str,
) -> None:
    run_dir.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f'{timestamp} risk_config {field}: {old} -> {new} | note="{note}"'
        for field, (old, new) in changes.items()
    ]
    with (run_dir / "config_changes.log").open("a") as f:
        for line in lines:
            f.write(line + "\n")


def read_env_value(path: Path, key: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$")
    for line in path.read_text().splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def write_env_values(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text().splitlines()
    remaining = dict(updates)
    new_lines = []
    for line in lines:
        matched_key = None
        for key in remaining:
            if line.startswith(f"{key}="):
                matched_key = key
                break
        if matched_key is not None:
            new_lines.append(f"{matched_key}={remaining.pop(matched_key)}")
        else:
            new_lines.append(line)
    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n")
