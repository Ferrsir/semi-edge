from __future__ import annotations

from pathlib import Path

import pytest
from check_unsafe_config import scan

pytestmark = [pytest.mark.governance, pytest.mark.safety]
ROOT = Path(__file__).resolve().parents[2]


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_repository_is_clean() -> None:
    assert [str(f) for f in scan(ROOT)] == []


@pytest.mark.parametrize(
    ("rel", "text", "needle"),
    [
        ("src/semiedge/x.py", "c = TradingClient(k, s, paper=False)\n", "paper=False"),
        ("src/semiedge/x.py", "c = make(url_override='https://x')\n", "url_override"),
        ("src/semiedge/x.py", "URL = 'https://api.alpaca.markets/v2'\n", "live Alpaca"),
        ("src/semiedge/x.py", "c = TradingClient(k, s, paper=True)\n", "outside"),
        ("deploy/docker-compose.yml", '  ALLOW_LIVE_TRADING: "true"\n', "must be false"),
        ("config/x.yaml", "TRADING_MODE: live\n", "unsupported"),
        (".env.example", "TRADING_MODE=paper\n", "default in .env.example"),
        (".env", "X=1\n", "committed environment file"),
        (".env.production", "X=1\n", "committed environment file"),
    ],
)
def test_detects_unsafe_patterns(tmp_path: Path, rel: str, text: str, needle: str) -> None:
    _write(tmp_path, rel, text)
    findings = [str(f) for f in scan(tmp_path)]
    assert any(needle in f for f in findings), findings


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("src/semiedge/x.py", "URL = 'https://paper-api.alpaca.markets'\n"),
        ("src/semiedge/x.py", "URL = 'https://data.alpaca.markets'\n"),
        ("src/semiedge/safety/broker.py", "c = TradingClient(k, s, paper=True)\n"),
        ("tests/safety/test_x.py", "TradingClient(k, s, paper=False)\n"),
        (".env.example", "TRADING_MODE=scanner\nALLOW_LIVE_TRADING=false\n"),
        ("src/semiedge/x.py", "# paper=False would be unsafe\n"),
    ],
)
def test_allows_safe_patterns(tmp_path: Path, rel: str, text: str) -> None:
    _write(tmp_path, rel, text)
    assert [str(f) for f in scan(tmp_path)] == []
