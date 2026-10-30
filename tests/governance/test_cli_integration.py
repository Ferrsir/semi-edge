"""Exercise safety scanning through the real CLI and filesystem boundary."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.safety]
CHECKER = Path(__file__).resolve().parents[2] / "scripts/governance/check_unsafe_config.py"


def test_scanner_cli_rejects_live_config_then_accepts_safe_default(tmp_path: Path) -> None:
    config = tmp_path / ".env.example"
    config.write_text("ALLOW_LIVE_TRADING=true\n", encoding="utf-8")
    command = [sys.executable, str(CHECKER), "--root", str(tmp_path)]
    rejected = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rejected.returncode == 1
    assert ".env.example" in rejected.stdout + rejected.stderr
    config.write_text("ALLOW_LIVE_TRADING=false\n", encoding="utf-8")
    accepted = subprocess.run(command, capture_output=True, text=True, check=False)
    assert accepted.returncode == 0
