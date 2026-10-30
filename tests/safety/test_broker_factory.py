from __future__ import annotations

from types import SimpleNamespace

import pytest

from semiedge.safety import SafetyViolation
from semiedge.safety.broker import (
    PAPER_TRADING_BASE_URL,
    assert_paper_client,
    create_paper_trading_client,
    validate_paper_key_id,
)

pytestmark = pytest.mark.safety


def test_factory_builds_paper_client_without_network() -> None:
    client = create_paper_trading_client("PKTESTKEY123", "secret")
    assert_paper_client(client)  # does not raise
    assert str(client._base_url.value) == PAPER_TRADING_BASE_URL


@pytest.mark.parametrize("key", ["AKLIVEKEY123", "", "pk-lowercase", "XXSOMETHING"])
def test_non_paper_key_ids_rejected(key: str) -> None:
    with pytest.raises(SafetyViolation, match="PAPER"):
        validate_paper_key_id(key)
    with pytest.raises(SafetyViolation):
        create_paper_trading_client(key, "secret")


def test_missing_secret_rejected() -> None:
    with pytest.raises(SafetyViolation, match="secret"):
        create_paper_trading_client("PKTESTKEY123", "")


@pytest.mark.parametrize(
    "url",
    ["https://api.alpaca.markets", "https://evil.example.com", "", None],
)
def test_assert_paper_client_rejects_non_paper_urls(url: str | None) -> None:
    with pytest.raises(SafetyViolation, match="Refusing"):
        assert_paper_client(SimpleNamespace(_base_url=url))


def test_live_alpaca_client_is_refused() -> None:
    from alpaca.trading.client import TradingClient

    live = TradingClient(api_key="AKLIVE", secret_key="x", paper=False)
    with pytest.raises(SafetyViolation):
        assert_paper_client(live)


def test_assert_paper_client_accepts_trailing_slash() -> None:
    assert_paper_client(SimpleNamespace(_base_url=PAPER_TRADING_BASE_URL + "/"))
