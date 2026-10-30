"""The real alpaca-py ``TradingClient`` (built by the paper-only factory) parsed end to end.

Offline: the SDK's HTTP session is replaced by ``FakeAlpacaTransport``; sockets are disabled.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from semiedge.broker.account import (
    AccountBlockReason,
    BrokerDataError,
    BrokerError,
    fetch_account_snapshot,
)
from semiedge.safety.broker import PAPER_TRADING_BASE_URL

pytestmark = pytest.mark.integration

Factory = Callable[[dict[str, tuple[int, object]]], tuple[Any, Any]]


def _routes(account: object, clock: object, positions: object) -> dict[str, tuple[int, object]]:
    return {"/account": (200, account), "/clock": (200, clock), "/positions": (200, positions)}


def test_snapshot_from_sdk_models(
    paper_client_factory: Factory,
    account_payload: dict[str, Any],
    clock_payload: dict[str, Any],
    positions_payload: list[dict[str, Any]],
    fake_credentials: Any,
) -> None:
    client, transport = paper_client_factory(
        _routes(account_payload, clock_payload, positions_payload)
    )

    snap = fetch_account_snapshot(client)

    assert transport.requests == [
        ("GET", f"{PAPER_TRADING_BASE_URL}/v2/account"),
        ("GET", f"{PAPER_TRADING_BASE_URL}/v2/clock"),
        ("GET", f"{PAPER_TRADING_BASE_URL}/v2/positions"),
    ]
    assert snap.account_number_masked == "****M9KD"
    assert fake_credentials.account_number not in repr(snap)
    assert snap.status == "ACTIVE"
    assert snap.currency == "USD"
    assert snap.equity == 100523.31
    assert snap.last_equity == 100012.40
    assert snap.cash == 90452.31
    assert snap.buying_power == 180904.62
    assert snap.long_market_value == 10071.0
    assert snap.pattern_day_trader is None  # field removed by Alpaca: unknown, not False
    assert (snap.trading_blocked, snap.account_blocked, snap.trade_suspended_by_user) == (
        False,
        False,
        False,
    )
    assert snap.is_tradable
    assert snap.market_is_open is True
    assert snap.as_of.tzinfo is UTC
    assert snap.as_of.replace(microsecond=0) == datetime(2026, 10, 30, 14, 15, 30, tzinfo=UTC)
    assert snap.next_close == datetime(2026, 10, 30, 20, 0, tzinfo=UTC)
    assert snap.next_open == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)

    amd, nvda = snap.positions
    assert (amd.symbol, amd.qty, amd.avg_entry_price, amd.current_price) == (
        "AMD",
        12.5,
        214.8,
        209.2,
    )
    assert (amd.market_value, amd.cost_basis, amd.unrealized_pl) == (2615.0, 2685.0, -70.0)
    assert (nvda.symbol, nvda.qty, nvda.side, nvda.asset_class) == (
        "NVDA",
        40.0,
        "long",
        "us_equity",
    )
    assert nvda.unrealized_plpc == pytest.approx(206 / 7250)
    assert sum(p.market_value for p in snap.positions) == snap.long_market_value

    for headers in transport.headers:  # credentials only travel in auth headers
        assert headers.get("APCA-API-KEY-ID") == fake_credentials.key_id
    assert fake_credentials.secret not in repr(snap)


def test_blocked_sdk_account_reports_reasons(
    paper_client_factory: Factory,
    account_payload: dict[str, Any],
    clock_payload: dict[str, Any],
) -> None:
    account_payload.update(status="ACCOUNT_CLOSED", account_blocked=True, trading_blocked=True)
    client, _ = paper_client_factory(_routes(account_payload, clock_payload, []))

    snap = fetch_account_snapshot(client)

    assert snap.block_reasons == (
        AccountBlockReason.STATUS_NOT_ACTIVE,
        AccountBlockReason.ACCOUNT_BLOCKED,
        AccountBlockReason.TRADING_BLOCKED,
    )
    assert not snap.is_tradable


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("equity", None),
        ("equity", "-1"),
        ("trading_blocked", None),
        ("account_blocked", None),
        ("trade_suspended_by_user", None),
        ("currency", None),
    ],
)
def test_sdk_payload_gaps_fail_closed(
    paper_client_factory: Factory,
    account_payload: dict[str, Any],
    clock_payload: dict[str, Any],
    field: str,
    value: object,
) -> None:
    account_payload[field] = value
    client, _ = paper_client_factory(_routes(account_payload, clock_payload, []))

    with pytest.raises(BrokerDataError, match=field):
        fetch_account_snapshot(client)


def test_http_error_is_sanitised(
    paper_client_factory: Factory,
    account_payload: dict[str, Any],
    fake_credentials: Any,
) -> None:
    secret_body = {"code": 40110000, "message": f"bad key {fake_credentials.key_id}"}
    client, transport = paper_client_factory(
        {"/account": (200, account_payload), "/clock": (401, secret_body)}
    )

    with pytest.raises(BrokerError) as caught:
        fetch_account_snapshot(client)

    assert caught.value.operation == "get_clock"
    assert caught.value.status_code == 401
    assert fake_credentials.key_id not in str(caught.value)
    assert "alpaca.markets" not in str(caught.value)
    assert [method for method, _ in transport.requests] == ["GET", "GET"]
