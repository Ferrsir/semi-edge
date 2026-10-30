"""The ONLY factory for Alpaca trading clients. Paper endpoint is hard-coded.

There is deliberately no parameter, environment variable or configuration value
that can select the live endpoint. Any future real-money capability requires a
separate, human-approved implementation (EPIC 12 is research only).
"""

from __future__ import annotations

from alpaca.common.enums import BaseURL
from alpaca.trading.client import TradingClient

from semiedge.safety.modes import SafetyViolation

PAPER_TRADING_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_TRADING_BASE_URL = "https://api.alpaca.markets"  # referenced only to refuse it
PAPER_KEY_ID_PREFIX = "PK"


def validate_paper_key_id(key_id: str) -> None:
    """Reject API key IDs that do not look like Alpaca PAPER keys.

    Alpaca paper key IDs start with ``PK`` and live key IDs with ``AK``. This is a
    defence-in-depth heuristic; the hard guarantee is the paper endpoint below.
    """
    if not key_id or not key_id.startswith(PAPER_KEY_ID_PREFIX):
        raise SafetyViolation(
            "The configured Alpaca key ID does not look like a PAPER key "
            f"(expected prefix {PAPER_KEY_ID_PREFIX!r}). Never use live brokerage credentials."
        )


def create_paper_trading_client(api_key: str, secret_key: str) -> TradingClient:
    """Build an Alpaca ``TradingClient`` bound to the PAPER endpoint, or raise.

    Always passes ``paper=True`` and never ``url_override``; then verifies the
    resolved base URL as a second, independent check.
    """
    validate_paper_key_id(api_key)
    if not secret_key:
        raise SafetyViolation("Alpaca paper secret key is missing.")

    client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
    assert_paper_client(client)
    return client


def assert_paper_client(client: object) -> None:
    """Raise unless ``client`` resolves to the Alpaca PAPER trading endpoint."""
    raw = getattr(client, "_base_url", None)
    # Never trust __str__, .value or equality on arbitrary objects: a string subclass
    # can display the paper URL while the SDK concatenates its underlying live URL.
    if raw is BaseURL.TRADING_PAPER:
        return
    if type(raw) is str and raw.rstrip("/") == PAPER_TRADING_BASE_URL:
        return
    raise SafetyViolation(f"Refusing trading client: only {PAPER_TRADING_BASE_URL} is allowed.")
