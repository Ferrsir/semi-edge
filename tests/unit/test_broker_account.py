"""Parsing and invariant tests for :mod:`semiedge.broker.account` (issue #13).

Offline: every client is a plain fake object whose ``get`` returns decoded JSON (dicts, lists,
strings), as the SDK's ``TradingClient.get`` does. No SDK client, no HTTP, no credentials.
"""

from __future__ import annotations

import dataclasses
from collections import OrderedDict
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

import semiedge.broker.account as account_module
from semiedge.broker.account import (
    AccountSnapshot,
    BrokerDataError,
    BrokerError,
    PositionSnapshot,
    fetch_account_snapshot,
    mask_account_number,
)
from semiedge.safety.broker import PAPER_TRADING_BASE_URL

EDT = timezone(timedelta(hours=-4))


def _account(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "account_number": "PA1234567890",
        "status": "ACTIVE",
        "currency": "USD",
        "equity": "100523.31",
        "last_equity": "100012.40",
        "cash": "90452.31",
        "buying_power": "180904.62",
        "long_market_value": "10071",
        "pattern_day_trader": None,
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
    }
    fields.update(overrides)
    return fields


def _clock(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "timestamp": "2026-10-30T10:15:30-04:00",
        "is_open": True,
        "next_open": "2026-11-02T14:30:00Z",
        "next_close": "2026-10-30T16:00:00-04:00",
    }
    fields.update(overrides)
    return fields


def _position(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "symbol": "NVDA",
        "qty": "40",
        "side": "long",
        "avg_entry_price": "181.25",
        "market_value": "7456",
        "cost_basis": "7250",
        "unrealized_pl": "206",
        "unrealized_plpc": "0.0284137931034483",
        "current_price": "186.4",
        "asset_class": "us_equity",
    }
    fields.update(overrides)
    return fields


_DEFAULT = object()


class FakeClient:
    def __init__(
        self, account: object = _DEFAULT, clock: object = _DEFAULT, positions: object = _DEFAULT
    ) -> None:
        self._base_url = PAPER_TRADING_BASE_URL
        self._account = _account() if account is _DEFAULT else account
        self._clock = _clock() if clock is _DEFAULT else clock
        self._positions = [] if positions is _DEFAULT else positions

    def get(self, path: str) -> object:
        return {"/account": self._account, "/clock": self._clock, "/positions": self._positions}[
            path
        ]


def _snapshot(**kwargs: Any) -> AccountSnapshot:
    return fetch_account_snapshot(FakeClient(**kwargs))


def test_valid_snapshot_parses_every_field() -> None:
    short = _position(
        symbol="AMD",
        qty="-10",
        side="short",
        avg_entry_price="200",
        market_value="-2100",
        cost_basis="-2000",
        unrealized_pl="-100",
        unrealized_plpc="-0.05",
        current_price="210",
    )
    snap = _snapshot(
        account=_account(pattern_day_trader=False),
        positions=[_position(), short],
    )

    assert snap.account_number_masked == "****7890"
    assert snap.status == "ACTIVE"
    assert snap.currency == "USD"
    assert snap.equity == 100523.31
    assert snap.last_equity == 100012.40
    assert snap.cash == 90452.31
    assert snap.buying_power == 180904.62
    assert snap.long_market_value == 10071.0
    assert snap.pattern_day_trader is False
    assert snap.market_is_open is True
    assert snap.as_of == datetime(2026, 10, 30, 14, 15, 30, tzinfo=UTC)
    assert snap.as_of.tzinfo is UTC
    assert snap.next_close == datetime(2026, 10, 30, 20, 0, tzinfo=UTC)
    assert snap.next_open == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
    assert [p.symbol for p in snap.positions] == ["AMD", "NVDA"]  # sorted
    amd, nvda = snap.positions
    assert (amd.side, amd.qty, amd.market_value) == ("short", -10.0, -2100.0)
    assert (nvda.side, nvda.qty, nvda.unrealized_plpc) == ("long", 40.0, 0.0284137931034483)
    assert snap.is_tradable


def test_missing_pattern_day_trader_is_unknown_not_false() -> None:
    account = _account()
    del account["pattern_day_trader"]
    assert _snapshot(account=account).pattern_day_trader is None
    assert _snapshot(account=_account(pattern_day_trader=None)).pattern_day_trader is None


def test_cash_may_be_negative_on_margin() -> None:
    assert _snapshot(account=_account(cash="-500.25")).cash == -500.25


@pytest.mark.parametrize(
    "value",
    [
        " 1",
        "1 ",
        "1_000",
        "١٢",
        "nan",
        "-inf",
        "Infinity",
        "1e999",
        "",
        "0x10",
        float("nan"),
        10**400,
    ],
)
def test_strict_decimal_parsing_rejects_lenient_float_inputs(value: object) -> None:
    with pytest.raises(BrokerDataError, match="equity"):
        _snapshot(account=_account(equity=value))


@pytest.mark.parametrize("value", [b"1", [1], {"v": 1}])
def test_decimal_fields_reject_non_numeric_types(value: object) -> None:
    with pytest.raises(BrokerDataError, match="must be a decimal string"):
        _snapshot(account=_account(equity=value))


@pytest.mark.parametrize("value", ["1.5e3", ".5", "+2", 7, 7.25])
def test_decimal_fields_accept_well_formed_numbers(value: object) -> None:
    assert _snapshot(account=_account(equity=value)).equity == float(value)  # type: ignore[arg-type]


def test_missing_field_is_reported_by_name() -> None:
    account = _account()
    del account["last_equity"]
    with pytest.raises(BrokerDataError, match="required field 'last_equity' is missing"):
        _snapshot(account=account)


@pytest.mark.parametrize("value", [1, 0, "true", "false"])
def test_pattern_day_trader_must_be_bool_when_present(value: object) -> None:
    with pytest.raises(BrokerDataError, match="pattern_day_trader"):
        _snapshot(account=_account(pattern_day_trader=value))


@pytest.mark.parametrize("value", [0, 1, 0.0, "false", "False", "true", "", [], {}])
@pytest.mark.parametrize("field", ["trading_blocked", "account_blocked", "trade_suspended_by_user"])
def test_blocking_flags_must_be_json_booleans(field: str, value: object) -> None:
    with pytest.raises(BrokerDataError, match=f"'{field}' must be a boolean"):
        _snapshot(account=_account(**{field: value}))


@pytest.mark.parametrize("field", ["trading_blocked", "account_blocked", "trade_suspended_by_user"])
def test_missing_or_null_blocking_flags_fail_closed(field: str) -> None:
    account = _account()
    del account[field]
    with pytest.raises(BrokerDataError, match=f"required field '{field}' is missing"):
        _snapshot(account=account)
    with pytest.raises(BrokerDataError, match=f"required field '{field}' is missing"):
        _snapshot(account=_account(**{field: None}))


@pytest.mark.parametrize("value", [123, "ACTIVE "])
def test_text_fields_reject_non_strings_and_padding(value: object) -> None:
    with pytest.raises(BrokerDataError, match="status"):
        _snapshot(account=_account(status=value))


@pytest.mark.parametrize("number", ["1234", "PA12345", "", 12345678])
def test_account_number_too_short_or_not_text_is_refused(number: object) -> None:
    with pytest.raises(BrokerDataError, match="account_number"):
        _snapshot(account=_account(account_number=number))


def test_mask_account_number_keeps_only_last_four() -> None:
    assert mask_account_number("PA123456") == "****3456"
    for too_short in ("1234", "AB123", "PA12345"):  # would reveal more than half
        with pytest.raises(BrokerDataError):
            mask_account_number(too_short)


@pytest.mark.parametrize(
    "raw", [None, [], "ACTIVE", 1, OrderedDict(_account()), type("Model", (), _account())()]
)
def test_account_must_be_a_decoded_json_object(raw: object) -> None:
    with pytest.raises(BrokerDataError, match="account: expected a JSON object"):
        _snapshot(account=raw)


@pytest.mark.parametrize("raw", [None, [], "open"])
def test_clock_must_be_a_decoded_json_object(raw: object) -> None:
    with pytest.raises(BrokerDataError, match="clock: expected a JSON object"):
        _snapshot(clock=raw)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("timestamp", "2026-10-30T14:15:00", "with an offset"),
        ("timestamp", 1793370930, "must be an RFC 3339 timestamp string"),
        ("timestamp", datetime(2026, 10, 30, 14, 15, tzinfo=UTC), "timestamp string"),
        ("timestamp", "2026-10-30 14:15:00Z", "with an offset"),
        ("timestamp", "2026-10-30", "with an offset"),
        ("timestamp", " 2026-10-30T14:15:00Z", "with an offset"),
        ("timestamp", "2026-10-30T14:15:00.1234567890Z", "with an offset"),
        ("timestamp", "2026-10-30T14:15:00+0000", "with an offset"),
        ("timestamp", "2026-10-30t14:15:00z", "with an offset"),
        ("timestamp", "2026-13-30T14:15:00Z", "not a valid RFC 3339"),
        ("next_open", "2026-11-02T25:30:00Z", "not a valid RFC 3339"),
        ("next_open", "2026-11-02T09:30:00+24:00", "not a valid RFC 3339"),
        # F-025: offset minutes above 59 must not be normalised into a different instant
        ("timestamp", "2026-10-30T10:15:30+00:60", "with an offset"),
        ("timestamp", "2026-10-30T10:15:30+01:99", "with an offset"),
        ("next_open", "2026-11-02T09:30:00-05:60", "with an offset"),
        ("next_open", "２０２６-11-02T09:30:00Z", "with an offset"),
        ("next_close", None, "missing"),
        ("is_open", "true", "must be a boolean"),
        ("is_open", 1, "must be a boolean"),
        ("is_open", 0, "must be a boolean"),
    ],
)
def test_clock_fields_fail_closed(field: str, value: object, match: str) -> None:
    with pytest.raises(BrokerDataError, match=match):
        _snapshot(clock=_clock(**{field: value}))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-30T14:15:30Z", datetime(2026, 10, 30, 14, 15, 30, tzinfo=UTC)),
        ("2026-10-30T10:15:30.5-04:00", datetime(2026, 10, 30, 14, 15, 30, 500000, tzinfo=UTC)),
        (
            "2026-10-30T10:15:30.123456789-04:00",  # Alpaca nanoseconds: truncated, not rounded
            datetime(2026, 10, 30, 14, 15, 30, 123456, tzinfo=UTC),
        ),
        ("2026-10-30T15:45:30+01:30", datetime(2026, 10, 30, 14, 15, 30, tzinfo=UTC)),
        ("2026-10-30T14:16:29+00:59", datetime(2026, 10, 30, 13, 17, 29, tzinfo=UTC)),
        ("2026-10-31T14:14:30+23:59", datetime(2026, 10, 30, 14, 15, 30, tzinfo=UTC)),
        ("2026-10-29T14:16:30-23:59", datetime(2026, 10, 30, 14, 15, 30, tzinfo=UTC)),
    ],
)
def test_rfc3339_timestamps_are_parsed_to_utc(value: str, expected: datetime) -> None:
    snap = _snapshot(clock=_clock(timestamp=value))
    assert snap.as_of == expected
    assert snap.as_of.tzinfo is UTC


@pytest.mark.parametrize("raw", [None, {"NVDA": 1}, "NVDA", 3, (), ("NVDA",)])
def test_positions_must_be_a_list(raw: object) -> None:
    with pytest.raises(BrokerDataError, match="positions: expected a list"):
        _snapshot(positions=raw)


@pytest.mark.parametrize("item", [None, "NVDA", ["NVDA"], 1])
def test_position_items_must_be_json_objects(item: object) -> None:
    with pytest.raises(BrokerDataError, match=r"positions\[0\]: expected a JSON object"):
        _snapshot(positions=[item])


def test_incomplete_position_object_fails_closed() -> None:
    with pytest.raises(BrokerDataError, match=r"position NVDA: required field 'qty' is missing"):
        _snapshot(positions=[{"symbol": "NVDA"}])


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"side": "flat"}, "side"),
        ({"side": "short"}, "qty sign contradicts side"),
        ({"market_value": "-1000"}, "market_value sign contradicts side"),
        ({"cost_basis": "-5"}, "cost_basis sign contradicts side"),
        ({"symbol": "nvda"}, r"positions\[0\]: 'symbol' is not a valid ticker"),
        ({"symbol": "X\x1b[2J\nY"}, r"positions\[0\]: 'symbol' is not a valid ticker"),
        ({"qty": "0"}, "qty sign contradicts side"),
        ({"avg_entry_price": "0"}, "must be positive"),
        ({"current_price": "-1"}, "must be positive"),
        ({"unrealized_pl": "NaN"}, "unrealized_pl"),
        ({"asset_class": ""}, "asset_class"),
        ({"symbol": None}, "symbol"),
    ],
)
def test_malformed_positions_fail_closed(overrides: dict[str, object], match: str) -> None:
    with pytest.raises(BrokerDataError, match=match):
        _snapshot(positions=[_position(**overrides)])


def test_duplicate_position_symbols_are_rejected() -> None:
    with pytest.raises(BrokerDataError, match="duplicate symbols"):
        _snapshot(positions=[_position(), _position()])


def test_broker_data_error_is_a_broker_error() -> None:
    assert issubclass(BrokerDataError, BrokerError)


# ------------------------------------------------------------------ direct construction


def _valid_snapshot() -> AccountSnapshot:
    return _snapshot(positions=[_position()])


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("account_number_masked", "PA1234567890", "account_number_masked"),
        ("account_number_masked", "****12345", "account_number_masked"),
        ("equity", float("inf"), "equity must be finite"),
        ("cash", "1", "cash must be a number"),
        ("cash", True, "cash must be a number"),
        ("trading_blocked", 0, "trading_blocked must be a boolean"),
        ("pattern_day_trader", 1, "pattern_day_trader must be a boolean or None"),
        ("market_is_open", None, "market_is_open must be a boolean"),
        ("as_of", datetime(2026, 1, 1, tzinfo=EDT), "as_of must be a timezone-aware UTC"),
        ("next_open", datetime(2026, 1, 1), "next_open must be"),  # noqa: DTZ001
        ("positions", [], "positions must be a tuple"),
        ("positions", ("NVDA",), "positions must be a tuple"),
        ("currency", "", "currency"),
        ("currency", "usd", "three-letter ISO 4217"),
        ("currency", "USDT", "three-letter ISO 4217"),
    ],
)
def test_snapshot_invariants_hold_on_direct_construction(
    field: str, value: object, match: str
) -> None:
    with pytest.raises(BrokerDataError, match=match):
        dataclasses.replace(_valid_snapshot(), **{field: value})


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("side", "both", "side must be"),
        ("qty", float("nan"), "qty must be finite"),
        ("symbol", " NVDA", "symbol"),
        ("symbol", "NVDA\nFAKE LOG LINE", "upper-case ticker symbol"),
    ],
)
def test_position_invariants_hold_on_direct_construction(
    field: str, value: object, match: str
) -> None:
    position = _valid_snapshot().positions[0]
    with pytest.raises(BrokerDataError, match=match):
        dataclasses.replace(position, **{field: value})


def test_snapshots_are_immutable() -> None:
    snapshot = _valid_snapshot()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.equity = 1.0  # type: ignore[misc]
    assert isinstance(snapshot.positions[0], PositionSnapshot)


# ------------------------------------------------------------------ failure status codes


class _StatusError(Exception):
    def __init__(self, status: object) -> None:
        super().__init__("boom")
        self._status = status

    @property
    def status_code(self) -> object:
        if isinstance(self._status, Exception):
            raise self._status
        return self._status


class _FailingClient(FakeClient):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def get(self, path: str) -> object:
        if path == "/clock":
            raise self._error
        return super().get(path)


@pytest.mark.parametrize(
    ("status", "expected"),
    [(403, 403), (True, None), (42, None), ("500", None), (ValueError("x"), None)],
)
def test_http_status_is_only_reported_when_plausible(status: object, expected: int | None) -> None:
    with pytest.raises(BrokerError) as caught:
        fetch_account_snapshot(_FailingClient(_StatusError(status)))
    assert caught.value.status_code == expected
    assert caught.value.operation == "GET /clock"
    assert "boom" not in str(caught.value)


def test_symbols_with_class_shares_crypto_and_options_are_accepted() -> None:
    symbols = ["BRK.B", "BTC/USD", "AAPL240119C00100000"]
    snap = _snapshot(positions=[_position(symbol=symbol) for symbol in symbols])
    assert [p.symbol for p in snap.positions] == sorted(symbols)


def test_unsorted_positions_are_rejected_on_direct_construction() -> None:
    snap = _snapshot(positions=[_position(symbol="AMD"), _position()])
    with pytest.raises(BrokerDataError, match="sorted"):
        dataclasses.replace(snap, positions=tuple(reversed(snap.positions)))


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"next_open": "2020-01-02T14:30:00Z"}, "must be after the timestamp"),
        ({"next_close": "2020-01-02T21:00:00Z"}, "must be after the timestamp"),
        ({"is_open": False}, "is_open contradicts"),
        ({"timestamp": "2026-11-02T14:30:00Z"}, "must be after the timestamp"),
    ],
)
def test_inconsistent_clock_fails_closed(overrides: dict[str, object], match: str) -> None:
    with pytest.raises(BrokerDataError, match=match):
        _snapshot(clock=_clock(**overrides))


def test_closed_market_clock_is_accepted() -> None:
    snap = _snapshot(
        clock=_clock(
            timestamp="2026-10-31T15:00:00Z",  # Saturday
            is_open=False,
            next_open="2026-11-02T14:30:00Z",
            next_close="2026-11-02T21:00:00Z",
        )
    )
    assert snap.market_is_open is False


def test_out_of_range_datetime_is_a_broker_data_error() -> None:
    with pytest.raises(BrokerDataError, match="outside the representable range"):
        _snapshot(clock=_clock(timestamp="0001-01-01T00:00:00+01:00"))


class _LyingStr(str):
    __slots__ = ()

    def __float__(self) -> float:
        return 1_000_000.0


@pytest.mark.parametrize("field", ["equity", "currency"])
def test_str_subclasses_are_rejected(field: str) -> None:
    with pytest.raises(BrokerDataError, match=field):
        _snapshot(account=_account(**{field: _LyingStr("100")}))


@pytest.mark.parametrize(
    ("field", "value"),
    [("equity", "SECRET-ish-1"), ("status", " SECRET-ish"), ("trading_blocked", "SECRET-ish")],
)
def test_rejected_values_are_never_echoed(field: str, value: object) -> None:
    with pytest.raises(BrokerDataError, match=field) as caught:
        _snapshot(account=_account(**{field: value}))
    assert "SECRET" not in str(caught.value)


def test_unexpected_parsing_failure_is_wrapped_without_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(value: object, label: str) -> None:
        raise ValueError("secret-ish response body")

    monkeypatch.setattr(account_module, "_check_text", explode)
    with pytest.raises(BrokerDataError, match=r"could not be parsed \(ValueError\)") as caught:
        _snapshot()
    assert "secret-ish" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
