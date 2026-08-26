from pathlib import Path

import pytest

from tradingsystem.dashboard import config_editing


CANDIDATE_UNIVERSE_YAML = """\
# The pool orchestration/ticker_selection.py's discovery screen draws
# candidates from. Freely editable — add/remove tickers here, takes effect
# next cycle.
tickers:
  - AAPL
  - MSFT
"""


def test_read_candidate_universe_tickers(tmp_path):
    path = tmp_path / "candidate_universe.yaml"
    path.write_text(CANDIDATE_UNIVERSE_YAML)

    assert config_editing.read_candidate_universe_tickers(path) == ["AAPL", "MSFT"]


def test_write_candidate_universe_preserves_header_comment(tmp_path):
    path = tmp_path / "candidate_universe.yaml"
    path.write_text(CANDIDATE_UNIVERSE_YAML)

    config_editing.write_candidate_universe(path, ["AAPL", "NVDA", "BRK.B"])

    content = path.read_text()
    assert "Freely editable" in content  # header comment survived the round-trip
    assert config_editing.read_candidate_universe_tickers(path) == ["AAPL", "NVDA", "BRK.B"]


def test_write_candidate_universe_rejects_empty_list(tmp_path):
    path = tmp_path / "candidate_universe.yaml"
    path.write_text(CANDIDATE_UNIVERSE_YAML)

    with pytest.raises(ValueError, match="empty"):
        config_editing.write_candidate_universe(path, [])


def test_write_candidate_universe_rejects_invalid_ticker(tmp_path):
    path = tmp_path / "candidate_universe.yaml"
    path.write_text(CANDIDATE_UNIVERSE_YAML)

    with pytest.raises(ValueError, match="aapl"):
        config_editing.write_candidate_universe(path, ["aapl"])  # lowercase, invalid


def test_write_candidate_universe_accepts_dotted_ticker(tmp_path):
    path = tmp_path / "candidate_universe.yaml"
    path.write_text(CANDIDATE_UNIVERSE_YAML)

    config_editing.write_candidate_universe(path, ["BRK.B"])  # must not raise

    assert config_editing.read_candidate_universe_tickers(path) == ["BRK.B"]


RISK_CONFIG_YAML = """\
# Risk control parameters, confirmed with the user in ARCHITECTURE.md section 4.
# Changing these is a deliberate decision, not a routine tuning knob.

max_position_pct: 0.10
cash_reserve_pct: 0.20
stop_loss_pct: 0.08

daily_drawdown_breaker_pct: 0.03
weekly_drawdown_breaker_pct: 0.08

stale_data_max_age_minutes: 15
"""


def test_read_risk_config_values(tmp_path):
    path = tmp_path / "risk_config.yaml"
    path.write_text(RISK_CONFIG_YAML)

    values = config_editing.read_risk_config_values(path)

    assert values["max_position_pct"] == 0.10
    assert values["stale_data_max_age_minutes"] == 15


def test_write_risk_config_preserves_header_comment_and_returns_changes(tmp_path):
    path = tmp_path / "risk_config.yaml"
    path.write_text(RISK_CONFIG_YAML)

    changes = config_editing.write_risk_config(path, {
        "max_position_pct": 0.12,
        "cash_reserve_pct": 0.20,  # unchanged — must not appear in the returned dict
    })

    assert changes == {"max_position_pct": (0.10, 0.12)}
    content = path.read_text()
    assert "not a routine tuning knob" in content  # header comment survived
    assert config_editing.read_risk_config_values(path)["max_position_pct"] == 0.12


@pytest.mark.parametrize("field,bad_value", [
    ("max_position_pct", 0.0),
    ("max_position_pct", 1.5),
    ("cash_reserve_pct", -0.1),
    ("stop_loss_pct", 0.0),
    ("daily_drawdown_breaker_pct", 1.1),
    ("weekly_drawdown_breaker_pct", 0.0),
    ("stale_data_max_age_minutes", 0),
    ("stale_data_max_age_minutes", -5),
])
def test_write_risk_config_rejects_out_of_range_values(tmp_path, field, bad_value):
    path = tmp_path / "risk_config.yaml"
    path.write_text(RISK_CONFIG_YAML)

    with pytest.raises(ValueError, match=field):
        config_editing.write_risk_config(path, {field: bad_value})


def test_append_config_change_log_one_line_per_field(tmp_path):
    changes = {
        "max_position_pct": (0.10, 0.12),
        "stop_loss_pct": (0.08, 0.06),
    }

    config_editing.append_config_change_log(tmp_path, changes, note="tightening risk after a red week")

    log_path = tmp_path / "config_changes.log"
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert "max_position_pct: 0.1 -> 0.12" in lines[0]
    assert 'note="tightening risk after a red week"' in lines[0]
    assert "stop_loss_pct: 0.08 -> 0.06" in lines[1]


def test_append_config_change_log_appends_to_existing_file(tmp_path):
    config_editing.append_config_change_log(tmp_path, {"a": (1, 2)}, note="first")
    config_editing.append_config_change_log(tmp_path, {"b": (3, 4)}, note="second")

    lines = (tmp_path / "config_changes.log").read_text().strip().splitlines()
    assert len(lines) == 2


ENV_FILE = """\
ALPACA_PAPER_API_KEY=super-secret-do-not-touch
DISCOVERY_SLOTS_PER_CYCLE=4
# a comment line
PRE_MARKET_CRON=35 9 * * mon-fri
"""


def test_read_env_value_finds_key(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV_FILE)

    assert config_editing.read_env_value(path, "DISCOVERY_SLOTS_PER_CYCLE") == "4"


def test_read_env_value_missing_key_returns_none(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV_FILE)

    assert config_editing.read_env_value(path, "NOT_PRESENT") is None


def test_write_env_values_replaces_only_matching_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV_FILE)

    config_editing.write_env_values(path, {"DISCOVERY_SLOTS_PER_CYCLE": "6"})

    lines = path.read_text().splitlines()
    assert lines[0] == "ALPACA_PAPER_API_KEY=super-secret-do-not-touch"  # untouched
    assert lines[1] == "DISCOVERY_SLOTS_PER_CYCLE=6"
    assert lines[2] == "# a comment line"  # untouched
    assert lines[3] == "PRE_MARKET_CRON=35 9 * * mon-fri"  # untouched


def test_write_env_values_appends_missing_key(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV_FILE)

    config_editing.write_env_values(path, {"WATCHDOG_CHECK_INTERVAL_MINUTES": "10"})

    assert config_editing.read_env_value(path, "WATCHDOG_CHECK_INTERVAL_MINUTES") == "10"
    assert "ALPACA_PAPER_API_KEY=super-secret-do-not-touch" in path.read_text()  # untouched
