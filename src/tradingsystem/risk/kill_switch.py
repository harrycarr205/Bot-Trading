"""Independent kill switch — an external file the validation layer checks before
every order. Not a flag the agent checks in its own loop (ARCHITECTURE.md §6):
a stuck/broken agent process can't bypass this because it never touches this file.
"""

from __future__ import annotations

from pathlib import Path


def is_kill_switch_active(kill_switch_file: str | Path) -> bool:
    return Path(kill_switch_file).exists()
