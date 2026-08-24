from tradingsystem.risk.kill_switch import is_kill_switch_active


def test_kill_switch_inactive_when_file_absent(tmp_path):
    path = tmp_path / "KILL_SWITCH"
    assert not is_kill_switch_active(path)


def test_kill_switch_active_when_file_present(tmp_path):
    path = tmp_path / "KILL_SWITCH"
    path.write_text("halt")
    assert is_kill_switch_active(path)


def test_kill_switch_accepts_string_path(tmp_path):
    path = tmp_path / "KILL_SWITCH"
    path.write_text("halt")
    assert is_kill_switch_active(str(path))
