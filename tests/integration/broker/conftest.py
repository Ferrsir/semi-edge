"""Offline Alpaca fixtures for the read-only account snapshot (issue #13).

Payloads mirror the JSON shapes of Alpaca's ``GET /v2/account``, ``/v2/clock`` and
``/v2/positions`` (numbers serialised as strings). Values are hand-chosen so that every expected
number in the tests can be checked by hand:

* NVDA: 40 sh @ 181.25, now 186.40 -> market value 7456.00, cost 7250.00, P/L +206.00,
  P/L % 206 / 7250 = 0.0284137931...
* AMD: 12.5 sh @ 214.80, now 209.20 -> market value 2615.00, cost 2685.00, P/L -70.00,
  P/L % -70 / 2685 = -0.0260707635...
* long market value 7456.00 + 2615.00 = 10071.00; equity = cash 90452.31 + 10071.00 = 100523.31.
* Clock: Friday 2026-10-30 10:15:30 EDT (UTC-4), market open; next close the same day 16:00 EDT
  = 20:00 UTC; next open Monday 2026-11-02 09:30 EST (UTC-5, DST ended 2026-11-01) = 14:30 UTC.

No network, no real credentials: the SDK's HTTP session is replaced by :class:`FakeAlpacaTransport`
and sockets are disabled.
"""

from __future__ import annotations

import copy
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import pytest
import requests

from semiedge.safety.broker import PAPER_TRADING_BASE_URL, create_paper_trading_client

FAKE_PAPER_KEY_ID = "PKFAKEKEYFORTESTS0001"
FAKE_PAPER_SECRET = "fake-paper-secret-for-tests-only"
ACCOUNT_NUMBER = "PA3B7XQ2M9KD"

_ACCOUNT: dict[str, Any] = {
    "id": "e6fe16f3-64a4-4921-8928-cadf02f92f98",
    "account_number": ACCOUNT_NUMBER,
    "status": "ACTIVE",
    "crypto_status": "ACTIVE",
    "currency": "USD",
    "buying_power": "180904.62",
    "regt_buying_power": "180904.62",
    "non_marginable_buying_power": "90452.31",
    "options_buying_power": "90452.31",
    "cash": "90452.31",
    "accrued_fees": "0",
    "pending_transfer_in": "0",
    "portfolio_value": "100523.31",
    "trading_blocked": False,
    "transfers_blocked": False,
    "account_blocked": False,
    "created_at": "2026-01-05T14:02:11.123456Z",
    "trade_suspended_by_user": False,
    "multiplier": "2",
    "shorting_enabled": True,
    "equity": "100523.31",
    "last_equity": "100012.40",
    "long_market_value": "10071",
    "short_market_value": "0",
    "initial_margin": "5035.5",
    "maintenance_margin": "3021.3",
    "last_maintenance_margin": "3001.1",
    "sma": "0",
    "options_approved_level": 0,
    "options_trading_level": 0,
    # pattern_day_trader / daytrade_count / daytrading_buying_power: removed by Alpaca on
    # 2026-07-06 (alpaca-py 0.44 documents them as deprecated, default None).
}

_CLOCK: dict[str, Any] = {
    "timestamp": "2026-10-30T10:15:30.123456789-04:00",
    "is_open": True,
    "next_open": "2026-11-02T09:30:00-05:00",
    "next_close": "2026-10-30T16:00:00-04:00",
}

_POSITIONS: list[dict[str, Any]] = [
    {
        "asset_id": "4ce9353c-66d1-46c2-898f-fce867ab0247",
        "symbol": "NVDA",
        "exchange": "NASDAQ",
        "asset_class": "us_equity",
        "asset_marginable": True,
        "qty": "40",
        "qty_available": "40",
        "avg_entry_price": "181.25",
        "side": "long",
        "market_value": "7456",
        "cost_basis": "7250",
        "unrealized_pl": "206",
        "unrealized_plpc": "0.0284137931034483",
        "unrealized_intraday_pl": "51.2",
        "unrealized_intraday_plpc": "0.0069149",
        "current_price": "186.4",
        "lastday_price": "185.12",
        "change_today": "0.0069144",
    },
    {
        "asset_id": "03fb07bb-5db1-4077-8dea-5d711b272625",
        "symbol": "AMD",
        "exchange": "NASDAQ",
        "asset_class": "us_equity",
        "asset_marginable": True,
        "qty": "12.5",
        "qty_available": "12.5",
        "avg_entry_price": "214.8",
        "side": "long",
        "market_value": "2615",
        "cost_basis": "2685",
        "unrealized_pl": "-70",
        "unrealized_plpc": "-0.0260707635009311",
        "unrealized_intraday_pl": "-12.5",
        "unrealized_intraday_plpc": "-0.0047562",
        "current_price": "209.2",
        "lastday_price": "210.2",
        "change_today": "-0.0047574",
    },
]


@pytest.fixture
def account_payload() -> dict[str, Any]:
    return copy.deepcopy(_ACCOUNT)


@pytest.fixture
def clock_payload() -> dict[str, Any]:
    return copy.deepcopy(_CLOCK)


@pytest.fixture
def positions_payload() -> list[dict[str, Any]]:
    return copy.deepcopy(_POSITIONS)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid(*args: object, **kwargs: object) -> None:
        pytest.fail("broker tests must never open a network connection")

    monkeypatch.setattr(socket, "socket", forbid)
    monkeypatch.setattr(socket, "create_connection", forbid)


def make_response(status: int, body: object, url: str) -> requests.Response:
    """Build the ``requests.Response`` Alpaca's REST client would receive."""
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode("utf-8")
    response.url = url
    response.reason = HTTPStatus(status).phrase
    response.encoding = "utf-8"
    response.headers["Content-Type"] = "application/json"
    return response


class FakeAlpacaTransport:
    """Replaces ``requests.Session.request`` on a real SDK client: canned JSON, no network.

    Records every request. Anything other than a GET to a known PAPER path fails the test with
    ``pytest.fail`` (a ``BaseException``), so the code under test cannot swallow it.
    """

    def __init__(self, routes: dict[str, tuple[int, object]]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str]] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, method: str, url: str, **opts: Any) -> requests.Response:
        self.requests.append((method, url))
        self.headers.append(dict(opts.get("headers") or {}))
        prefix = PAPER_TRADING_BASE_URL + "/v2"
        if method != "GET" or not url.startswith(prefix):
            pytest.fail(f"unexpected broker request {method} {url}")
        path = url.removeprefix(prefix)
        if path not in self.routes:
            pytest.fail(f"unexpected broker path {path}")
        status, body = self.routes[path]
        return make_response(status, body, url)


@pytest.fixture
def paper_client_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[dict[str, tuple[int, object]]], tuple[Any, FakeAlpacaTransport]]:
    """Real ``TradingClient`` from the paper-only factory, wired to a fake HTTP transport."""

    def build(routes: dict[str, tuple[int, object]]) -> tuple[Any, FakeAlpacaTransport]:
        client = create_paper_trading_client(FAKE_PAPER_KEY_ID, FAKE_PAPER_SECRET)
        client._retry = 0  # never sleep/retry inside tests
        transport = FakeAlpacaTransport(routes)
        monkeypatch.setattr(client._session, "request", transport)
        return client, transport

    return build


@dataclass(frozen=True)
class FakeCredentials:
    key_id: str
    secret: str
    account_number: str


@pytest.fixture
def fake_credentials() -> FakeCredentials:
    """Fake key, secret and unmasked account number used by the fixtures above."""
    return FakeCredentials(FAKE_PAPER_KEY_ID, FAKE_PAPER_SECRET, ACCOUNT_NUMBER)
