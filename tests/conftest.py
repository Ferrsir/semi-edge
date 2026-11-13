from __future__ import annotations

from pathlib import Path

import pytest

# Shared SQLite state-store fixtures (issue #21): tests/state_support.py.
pytest_plugins = ("state_support",)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"

_ENV_VARS = (
    "TRADING_MODE",
    "ENABLE_ORDER_SUBMISSION",
    "ALLOW_LIVE_TRADING",
    "ALPACA_PAPER_API_KEY",
    "ALPACA_PAPER_SECRET_KEY",
    "DISCORD_WEBHOOK_URL",
    "FINNHUB_API_KEY",
    "ALPHAVANTAGE_API_KEY",
    "SEMIEDGE_CONFIG_DIR",
    "SEMIEDGE_DATA_DIR",
    "SEMIEDGE_LOG_LEVEL",
    "SEMIEDGE_LOG_FORMAT",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tests never see the developer's real environment or .env file."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # AppSettings reads ".env" relative to CWD
    assert not (tmp_path / ".env").exists()


@pytest.fixture
def config_dir() -> Path:
    return CONFIG_DIR
