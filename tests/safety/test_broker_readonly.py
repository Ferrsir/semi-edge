"""Safety invariants of the read-only PAPER account snapshot (issue #13).

* ``fetch_account_snapshot`` touches nothing on the client except the attribute the paper guard
  inspects (``_base_url``) and ``get``, called exactly once for each of the three read-only paths
  ``/account``, ``/clock`` and ``/positions`` with no other argument. It reads the raw JSON rather
  than SDK models, so lax SDK coercion cannot hide a malformed blocking flag (F-009).
* A client bound to anything but the PAPER endpoint is refused before any request.
* No order (or other non-read) client method is referenced anywhere in ``semiedge.broker``.
* Broker failures are wrapped in ``BrokerError`` without credentials, URLs or response bodies.
* Missing or malformed values fail closed; nothing defaults to ``0``/``False``.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import pkgutil
import re
import socket
import traceback
from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest
import requests
from alpaca.common.enums import BaseURL
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient

import semiedge
import semiedge.broker
from semiedge.broker.account import (
    AccountBlockReason,
    BrokerDataError,
    BrokerError,
    ReadOnlyTradingClient,
    fetch_account_snapshot,
)
from semiedge.safety import SafetyViolation
from semiedge.safety.broker import (
    LIVE_TRADING_BASE_URL,
    PAPER_TRADING_BASE_URL,
    create_paper_trading_client,
)

pytestmark = pytest.mark.safety

READ_METHOD = "get"  # the only client method: RESTClient.get, i.e. an HTTP GET
READ_PATHS = ("/account", "/clock", "/positions")  # the only paths it may be called with
PAPER_GUARD_ATTRIBUTE = "_base_url"  # the only client attribute assert_paper_client inspects
FAKE_KEY_ID = "PKFAKESAFETYKEY0001"
FAKE_SECRET = "fake-safety-secret-value"
ACCOUNT_NUMBER = "PA3SAFETY0042"


class ForbiddenClientAccess(BaseException):
    """Raised by :class:`StrictReadOnlyClient`.

    A ``BaseException`` so that neither ``except Exception`` in the code under test nor a
    ``getattr(client, name, default)`` (which only swallows ``AttributeError``) can hide it.
    """


def _account(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "account_number": ACCOUNT_NUMBER,
        "status": "ACTIVE",
        "currency": "USD",
        "equity": "100000",
        "last_equity": "99500.5",
        "cash": "100000",
        "buying_power": "200000",
        "long_market_value": "0",
        "pattern_day_trader": None,
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
    }
    fields.update(overrides)
    return fields


def _clock() -> dict[str, object]:
    return {
        "timestamp": "2026-10-30T14:15:00Z",
        "is_open": True,
        "next_open": "2026-11-02T14:30:00Z",
        "next_close": "2026-10-30T20:00:00Z",
    }


class StrictReadOnlyClient:
    """A broker client that refuses every attribute except the permitted read surface.

    Only ``get`` is answered, and only with exactly one positional argument from
    :data:`READ_PATHS`; it returns decoded JSON like the SDK's ``RESTClient.get``.

    Every instance attribute access - permitted or not, dunder or not - is recorded before it is
    answered, so a forbidden access is detected even if the code under test catches the error.
    """

    def __init__(
        self,
        base_url: object = PAPER_TRADING_BASE_URL,
        *,
        account: object | None = None,
        positions: object = (),
        fail_with: dict[str, Exception] | None = None,
    ) -> None:
        state = {
            "base_url": base_url,
            "accessed": [],
            "calls": [],
            "responses": {
                "/account": account if account is not None else _account(),
                "/clock": _clock(),
                "/positions": list(positions),  # type: ignore[call-overload]
            },
            "fail_with": fail_with or {},
        }
        object.__setattr__(self, "_strict_state", state)

    def __getattribute__(self, name: str) -> Any:
        state = object.__getattribute__(self, "_strict_state")
        state["accessed"].append(name)
        if name == PAPER_GUARD_ATTRIBUTE:
            return state["base_url"]
        if name == READ_METHOD:

            def read(*args: object, **kwargs: object) -> object:
                if kwargs or len(args) != 1 or type(args[0]) is not str:
                    raise ForbiddenClientAccess("get must be called with exactly one path")
                path = args[0]
                if path not in READ_PATHS:
                    raise ForbiddenClientAccess(f"forbidden broker path {path!r}")
                state["calls"].append(path)
                if path in state["fail_with"]:
                    raise state["fail_with"][path]
                return state["responses"][path]

            return read
        raise ForbiddenClientAccess(f"forbidden client attribute {name!r}")

    def __setattr__(self, name: str, value: object) -> None:
        raise ForbiddenClientAccess(f"forbidden client attribute assignment {name!r}")

    def __delattr__(self, name: str) -> None:
        raise ForbiddenClientAccess(f"forbidden client attribute deletion {name!r}")


def _state(client: StrictReadOnlyClient) -> dict[str, Any]:
    state: dict[str, Any] = object.__getattribute__(client, "_strict_state")
    return state


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid(*args: object, **kwargs: object) -> None:
        pytest.fail("safety tests must never open a network connection")

    monkeypatch.setattr(socket, "socket", forbid)
    monkeypatch.setattr(socket, "create_connection", forbid)


# ------------------------------------------------------------------ strict client harness


@pytest.mark.parametrize(
    "name",
    [
        "submit_order",
        "cancel_orders",
        "cancel_order_by_id",
        "replace_order_by_id",
        "close_position",
        "close_all_positions",
        "get_orders",
        "get_account",  # SDK model methods: lax coercion would hide malformed flags (F-009)
        "get_clock",
        "get_all_positions",
        "post",
        "delete",
        "_session",
        "__dict__",
        "__class__",
    ],
)
def test_strict_client_refuses_and_records_any_other_attribute(name: str) -> None:
    client = StrictReadOnlyClient()
    with pytest.raises(ForbiddenClientAccess):
        getattr(client, name)
    with pytest.raises(ForbiddenClientAccess):  # a getattr default cannot hide it
        getattr(client, name, None)
    try:  # nor can a broad ``except Exception``
        getattr(client, name)
    except Exception:  # pragma: no cover - must not happen
        pytest.fail("ForbiddenClientAccess was caught by `except Exception`")
    except ForbiddenClientAccess:
        pass
    assert _state(client)["accessed"] == [name, name, name]
    with pytest.raises(ForbiddenClientAccess):
        client.anything = 1
    for args, kwargs in (
        ((), {}),
        (("/orders",), {}),
        (("/account/activities",), {}),
        (("/account", {"status": "all"}), {}),
        (("/account",), {"data": {"x": 1}}),
        (("account",), {}),
    ):
        with pytest.raises(ForbiddenClientAccess):  # get refuses any other path or argument
            client.get(*args, **kwargs)
    assert _state(client)["calls"] == []


# ------------------------------------------------------------------ read-only surface


def test_snapshot_touches_only_paper_guard_and_three_read_paths() -> None:
    client = StrictReadOnlyClient()

    snapshot = fetch_account_snapshot(client)  # type: ignore[arg-type]

    state = _state(client)
    # the base URL is read by the strict type-exact check and by assert_paper_client
    assert state["accessed"] == [PAPER_GUARD_ATTRIBUTE] * 2 + [READ_METHOD] * 3
    assert state["calls"] == list(READ_PATHS)
    assert snapshot.equity == 100000.0
    assert snapshot.positions == ()


def test_read_only_protocol_declares_only_get_of_the_three_paths() -> None:
    declared = {name for name in vars(ReadOnlyTradingClient) if not name.startswith("_")}
    assert declared == {READ_METHOD}
    hints = get_type_hints(ReadOnlyTradingClient.get)
    assert set(hints) == {"path", "return"}
    assert get_args(hints["path"]) == READ_PATHS


def _broker_modules() -> list[str]:
    names = ["semiedge.broker"]
    names += [
        info.name
        for info in pkgutil.walk_packages(semiedge.broker.__path__, prefix="semiedge.broker.")
    ]
    return sorted(names)


def _referenced_identifiers(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.alias):
            found.update({node.name.rsplit(".", 1)[-1], node.asname or ""})
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)  # catches getattr(client, "submit_order")
    return found


def test_broker_package_references_no_order_or_other_client_method() -> None:
    """Phase 1: ``semiedge.broker`` is read-only.

    Every public or private method of the SDK ``TradingClient`` (orders, positions closing,
    watchlists, the other raw HTTP verbs, the model-returning reads, ...) except ``get`` must be
    absent from the source; ``get`` itself is pinned to the three paths by the usage scan below.
    When the order lifecycle (#21) lands, narrow this scan to the read-only modules
    deliberately, in a reviewed change.
    """
    client_methods = {
        name
        for name in dir(TradingClient)
        if callable(getattr(TradingClient, name)) and not name.startswith("__")
    }
    assert {"submit_order", "cancel_orders", "close_all_positions", "post"} <= client_methods
    assert READ_METHOD in client_methods
    forbidden = client_methods - {READ_METHOD}

    modules = _broker_modules()
    assert "semiedge.broker.account" in modules
    for module_name in modules:
        spec = importlib.util.find_spec(module_name)
        assert spec is not None
        assert spec.origin is not None
        with open(spec.origin, encoding="utf-8") as handle:
            referenced = _referenced_identifiers(handle.read())
        assert referenced & forbidden == set(), module_name


# Anything that could reach the network or the SDK session around the three reads, or resolve a
# name dynamically. ``semiedge.broker`` must not reference any of these.
_BYPASS_IDENTIFIERS = {
    "_session",
    "request",
    "requests",
    "urllib",
    "urllib3",
    "http",
    "httpx",
    "aiohttp",
    "socket",
    "importlib",
    "__import__",
    "eval",
    "exec",
    "__getattribute__",
}
# Calls that may receive the client itself: the two paper-endpoint guards, plus
# ``getattr(client, "_base_url", None)`` inside the type-exact guard.
_CLIENT_CONSUMERS = {"assert_paper_client", "_assert_exact_paper_endpoint"}


def _client_usage_violations(source: str) -> list[str]:
    """Every use of a name ``client`` that is not an allowed read or a paper-guard call.

    The only allowed read is ``client.get("<path>")`` with a single string literal from
    :data:`READ_PATHS`: no variable path, no query data, no keyword argument.
    """
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    violations: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id == "client"):
            continue
        parent = parents[node]
        if isinstance(parent, ast.Attribute) and parent.attr == READ_METHOD:
            grand = parents[parent]
            if (
                isinstance(grand, ast.Call)
                and grand.func is parent
                and not grand.keywords
                and len(grand.args) == 1
                and isinstance(grand.args[0], ast.Constant)
                and grand.args[0].value in READ_PATHS
            ):
                continue  # client.get("/account") etc.
        if isinstance(parent, ast.Call) and parent.args and parent.args[0] is node:
            func = parent.func
            if isinstance(func, ast.Name) and func.id in _CLIENT_CONSUMERS:
                continue
            if (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(parent.args) >= 2
                and isinstance(parent.args[1], ast.Constant)
                and parent.args[1].value == PAPER_GUARD_ATTRIBUTE
            ):
                continue
        violations.append(f"line {node.lineno}: {ast.unparse(parent)}")
    return violations


def _module_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for module_name in _broker_modules():
        spec = importlib.util.find_spec(module_name)
        assert spec is not None
        assert spec.origin is not None
        with open(spec.origin, encoding="utf-8") as handle:
            sources[module_name] = handle.read()
    return sources


def test_broker_package_uses_the_client_only_for_the_three_reads() -> None:
    """Allowlist: ``client`` may only ``get`` the three paths or be handed to a paper guard.

    Aliasing (``c = client``), passing it to another helper, computed ``getattr`` names or raw
    session access are all rejected, so a renamed or string-built order call cannot slip in.
    """
    for module_name, source in _module_sources().items():
        assert _client_usage_violations(source) == [], module_name
        assert _referenced_identifiers(source) & _BYPASS_IDENTIFIERS == set(), module_name


@pytest.mark.parametrize(
    "snippet",
    [
        "def f(client):\n    getattr(client, 'submit_' + 'order')(1)",
        "def f(client):\n    client._session.request('POST', 'u')",
        "def f(client):\n    c = client\n    c.submit_order(1)",
        "def f(client):\n    helper(client)",
        "def f(client):\n    client.get_account()",
        "def f(client):\n    client.get('/orders')",
        "def f(client):\n    client.get('/account', {'a': 1})",
        "def f(client):\n    client.get('/account', data=1)",
        "def f(client, path):\n    client.get(path)",
        "def f(client):\n    client.get('/' + 'orders')",
        "def f(client):\n    client.post('/account')",
        "def f(client):\n    return client.get",
    ],
)
def test_client_usage_scan_catches_bypasses(snippet: str) -> None:
    assert _client_usage_violations(snippet) != []


@pytest.mark.parametrize(
    "snippet",
    [
        "import requests\ndef f(): requests.request('DELETE', 'u')",
        "def f(x):\n    x._session.request('POST', 'u')",
        "import importlib\ndef f(): importlib.import_module('alpaca')",
    ],
)
def test_bypass_identifier_scan_catches_raw_http(snippet: str) -> None:
    assert _referenced_identifiers(snippet) & _BYPASS_IDENTIFIERS != set()


_ORDER_METHOD = re.compile(r"order|cancel|close|replace|exercise")


def test_no_module_in_semiedge_references_an_order_method() -> None:
    """Phase 1 has no order code anywhere in ``src/semiedge`` (not only ``semiedge.broker``).

    The order lifecycle (#21) must change this test deliberately, in a reviewed PR.
    """
    order_methods = {
        name
        for name in dir(TradingClient)
        if callable(getattr(TradingClient, name)) and _ORDER_METHOD.search(name)
    }
    assert {"submit_order", "cancel_orders", "replace_order_by_id"} <= order_methods
    package_root = Path(semiedge.__file__).parent
    for path in sorted(package_root.rglob("*.py")):
        referenced = _referenced_identifiers(path.read_text(encoding="utf-8"))
        assert referenced & order_methods == set(), str(path)


# ------------------------------------------------------------------ paper-only guard


@pytest.mark.parametrize(
    "base_url",
    [
        LIVE_TRADING_BASE_URL,
        BaseURL.TRADING_LIVE,
        BaseURL.BROKER_PRODUCTION,
        "http://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets.evil.example",
        "https://user:pw@paper-api.alpaca.markets",
        "",
        None,
    ],
)
def test_non_paper_client_is_rejected_before_any_call(base_url: object) -> None:
    client = StrictReadOnlyClient(base_url)

    with pytest.raises(SafetyViolation, match="Refusing"):
        fetch_account_snapshot(client)  # type: ignore[arg-type]

    assert _state(client)["accessed"] == [PAPER_GUARD_ATTRIBUTE]
    assert _state(client)["calls"] == []


def test_live_sdk_client_is_rejected_before_any_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = TradingClient(api_key="AKFAKELIVEKEY0001", secret_key="fake", paper=False)

    def no_request(*args: object, **kwargs: object) -> None:
        pytest.fail("an HTTP request was attempted with a live-endpoint client")

    monkeypatch.setattr(live._session, "request", no_request)
    with pytest.raises(SafetyViolation):
        fetch_account_snapshot(live)


class _LyingUrl(str):
    """Looks like the paper URL to ``str()`` but the SDK concatenates the real (live) value."""

    __slots__ = ()

    def __str__(self) -> str:
        return PAPER_TRADING_BASE_URL


def test_str_subclass_base_url_cannot_disguise_a_live_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = create_paper_trading_client(FAKE_KEY_ID, FAKE_SECRET)
    client._base_url = _LyingUrl(LIVE_TRADING_BASE_URL)

    def no_request(*args: object, **kwargs: object) -> None:
        pytest.fail("an HTTP request was attempted with a disguised live endpoint")

    monkeypatch.setattr(client._session, "request", no_request)
    with pytest.raises(SafetyViolation, match="Refusing"):
        fetch_account_snapshot(client)


@pytest.mark.parametrize("base_url", [BaseURL.TRADING_PAPER, PAPER_TRADING_BASE_URL + "/"])
def test_exact_paper_endpoint_forms_are_accepted(base_url: object) -> None:
    snapshot = fetch_account_snapshot(StrictReadOnlyClient(base_url))  # type: ignore[arg-type]
    assert snapshot.is_tradable


def _json_response(status: int, body: object, url: str) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = requests.compat.json.dumps(body).encode("utf-8")
    response.url = url
    response.reason = "OK" if status < 400 else "Unauthorized"
    response.encoding = "utf-8"
    return response


def test_paper_sdk_client_sends_exactly_three_get_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    client = create_paper_trading_client(FAKE_KEY_ID, FAKE_SECRET)
    bodies: dict[str, object] = {
        "/v2/account": {
            "id": "a3d9c3a4-3b0e-4c5e-9d7e-0b8a3d0f8e11",
            **_account(),
        },
        "/v2/clock": {
            "timestamp": "2026-10-30T10:15:00-04:00",
            "is_open": True,
            "next_open": "2026-11-02T09:30:00-05:00",
            "next_close": "2026-10-30T16:00:00-04:00",
        },
        "/v2/positions": [],
    }
    seen: list[tuple[str, str]] = []

    def transport(method: str, url: str, **opts: object) -> requests.Response:
        seen.append((method, url))
        path = url.removeprefix(PAPER_TRADING_BASE_URL)
        if method != "GET" or path not in bodies:
            pytest.fail(f"unexpected broker request {method} {url}")
        return _json_response(200, bodies[path], url)

    monkeypatch.setattr(client._session, "request", transport)

    snapshot = fetch_account_snapshot(client)

    assert seen == [
        ("GET", f"{PAPER_TRADING_BASE_URL}/v2/account"),
        ("GET", f"{PAPER_TRADING_BASE_URL}/v2/clock"),
        ("GET", f"{PAPER_TRADING_BASE_URL}/v2/positions"),
    ]
    assert snapshot.account_number_masked == "****0042"


# ------------------------------------------------------------------ failure handling


def _http_api_error(status: int, operation_path: str) -> APIError:
    url = f"{PAPER_TRADING_BASE_URL}/v2{operation_path}"
    body = {"code": 40110000, "message": f"key {FAKE_KEY_ID} secret {FAKE_SECRET} rejected"}
    response = _json_response(status, body, url)
    http_error = requests.HTTPError(f"{status} Client Error: Unauthorized for url: {url}")
    http_error.response = response
    return APIError(response.text, http_error)


def _leaky_errors(path: str) -> list[tuple[Exception, int | None]]:
    url = f"{PAPER_TRADING_BASE_URL}/v2{path}"
    return [
        (_http_api_error(401, path), 401),
        (
            requests.ConnectionError(
                f"HTTPSConnectionPool(host='paper-api.alpaca.markets', port=443): "
                f"Max retries exceeded with url: {url} (key={FAKE_KEY_ID})"
            ),
            None,
        ),
        (RuntimeError(f"GET {url} APCA-API-SECRET-KEY={FAKE_SECRET}"), None),
    ]


@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.parametrize("case", [0, 1, 2])
def test_client_failures_are_wrapped_without_credentials_or_urls(path: str, case: int) -> None:
    error, status = _leaky_errors(path)[case]
    operation = f"GET {path}"
    client = StrictReadOnlyClient(fail_with={path: error})

    with pytest.raises(BrokerError) as caught:
        fetch_account_snapshot(client)  # type: ignore[arg-type]

    failure = caught.value
    assert type(failure) is BrokerError
    assert failure.operation == operation
    assert failure.status_code == status
    assert failure.__cause__ is None
    assert failure.__context__ is None  # the leaky original is not even implicitly chained
    rendered = "".join(traceback.format_exception(failure)) + repr(failure) + str(failure)
    for leak in (FAKE_KEY_ID, FAKE_SECRET, "alpaca.markets", "http", "/v2", "rejected"):
        assert leak not in rendered
    assert operation in str(failure)
    assert type(error).__name__ in str(failure)
    calls = _state(client)["calls"]
    assert calls == list(READ_PATHS[: READ_PATHS.index(path) + 1])


# ------------------------------------------------------------------ fail closed


@pytest.mark.parametrize("value", [None, "", "NaN", "inf", "abc", True])
@pytest.mark.parametrize("field", ["equity", "cash", "buying_power"])
def test_unusable_money_values_fail_closed_never_default(field: str, value: object) -> None:
    client = StrictReadOnlyClient(account=_account(**{field: value}))
    with pytest.raises(BrokerDataError, match=field):
        fetch_account_snapshot(client)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, "false", 0, 1, "true", "missing"])
@pytest.mark.parametrize("field", ["trading_blocked", "account_blocked", "trade_suspended_by_user"])
def test_unknown_block_state_fails_closed(field: str, value: object) -> None:
    account = _account(**{field: value})
    if value == "missing":
        del account[field]
    client = StrictReadOnlyClient(account=account)
    with pytest.raises(BrokerDataError, match=field):
        fetch_account_snapshot(client)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["equity", "last_equity", "buying_power", "long_market_value"])
def test_negative_account_totals_fail_closed(field: str) -> None:
    client = StrictReadOnlyClient(account=_account(**{field: "-0.01"}))
    with pytest.raises(BrokerDataError, match=f"{field} must not be negative"):
        fetch_account_snapshot(client)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, "", " USD"])
def test_missing_or_malformed_currency_fails_closed(value: object) -> None:
    client = StrictReadOnlyClient(account=_account(currency=value))
    with pytest.raises(BrokerDataError, match="currency"):
        fetch_account_snapshot(client)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"account_blocked": True}, [AccountBlockReason.ACCOUNT_BLOCKED]),
        ({"trading_blocked": True}, [AccountBlockReason.TRADING_BLOCKED]),
        ({"trade_suspended_by_user": True}, [AccountBlockReason.TRADE_SUSPENDED_BY_USER]),
        ({"status": "ACCOUNT_CLOSED"}, [AccountBlockReason.STATUS_NOT_ACTIVE]),
        ({"currency": "EUR"}, [AccountBlockReason.UNSUPPORTED_CURRENCY]),
        (
            {"status": "DISABLED", "account_blocked": True, "trading_blocked": True},
            [
                AccountBlockReason.STATUS_NOT_ACTIVE,
                AccountBlockReason.ACCOUNT_BLOCKED,
                AccountBlockReason.TRADING_BLOCKED,
            ],
        ),
    ],
)
def test_blocked_account_reports_explicit_reason_codes(
    overrides: dict[str, object], expected: list[AccountBlockReason]
) -> None:
    snapshot = fetch_account_snapshot(
        StrictReadOnlyClient(account=_account(**overrides))  # type: ignore[arg-type]
    )
    assert list(snapshot.block_reasons) == expected
    assert snapshot.is_tradable is False


def test_unblocked_active_usd_account_is_tradable() -> None:
    snapshot = fetch_account_snapshot(StrictReadOnlyClient())  # type: ignore[arg-type]
    assert snapshot.block_reasons == ()
    assert snapshot.is_tradable is True


def test_account_number_is_never_exposed_unmasked() -> None:
    snapshot = fetch_account_snapshot(StrictReadOnlyClient())  # type: ignore[arg-type]
    assert snapshot.account_number_masked == "****0042"
    assert ACCOUNT_NUMBER not in repr(snapshot)
    assert ACCOUNT_NUMBER not in str(snapshot)
