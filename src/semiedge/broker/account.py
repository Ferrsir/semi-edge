"""Read-only Alpaca PAPER account snapshot (issue #13).

:func:`fetch_account_snapshot` first verifies that the client is bound to the PAPER endpoint
(a type-exact base-URL check, then :func:`semiedge.safety.broker.assert_paper_client`) and then
makes exactly three read requests through the SDK's ``get``: ``GET /v2/account``, ``/v2/clock``
and ``/v2/positions`` (the same requests ``get_account``, ``get_clock`` and ``get_all_positions``
send). Nothing here can change broker state. The client parameter is typed with
:class:`ReadOnlyTradingClient`, a protocol whose only method is ``get`` restricted to those three
literal paths, so mypy rejects any other broker call; ``tests/safety/test_broker_readonly.py``
proves the same at runtime with a client that refuses every other attribute, argument and path.

Raw JSON, not SDK models (F-009). The SDK's pydantic models coerce in lax mode (``0`` or
``"false"`` become ``False`` for a blocking flag, a number becomes a datetime), which would hide
a malformed blocking state before it could be rejected. The decoded JSON is therefore validated
directly with exact JSON types: flags must be JSON booleans, text must be JSON strings and
timestamps must be RFC 3339 strings with an explicit offset.

Fail closed. Alpaca serialises money and quantities as decimal strings. Every field the snapshot
needs is parsed strictly: a missing, ``null``, mistyped, unparseable or non-finite value raises
:class:`BrokerDataError` and is never replaced by a default such as ``0``. Error messages name the
field and the problem but never echo raw values, credentials or URLs.

The three requests are not atomic: account, clock and positions are read at slightly different
instants, so account totals (e.g. ``long_market_value``) need not equal the sum over
``positions``. ``as_of`` is the broker clock timestamp, converted to UTC.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Literal, Protocol

from alpaca.common.enums import BaseURL

from semiedge.safety.broker import PAPER_TRADING_BASE_URL, assert_paper_client
from semiedge.safety.modes import SafetyViolation

PositionSide = Literal["long", "short"]
# The only broker paths this module may request (relative to ``/v2``), all with ``GET``.
AccountReadPath = Literal["/account", "/clock", "/positions"]

ACCOUNT_MASK_PREFIX: Final = "****"
TRADABLE_ACCOUNT_STATUS: Final = "ACTIVE"
SUPPORTED_CURRENCY: Final = "USD"
# Account totals that can never be negative for a cash/Reg-T account. ``cash`` is deliberately
# absent: it is negative while margin is borrowed.
NON_NEGATIVE_ACCOUNT_FIELDS: Final = ("equity", "last_equity", "buying_power", "long_market_value")
VISIBLE_ACCOUNT_CHARS: Final = 4
MIN_ACCOUNT_NUMBER_CHARS: Final = 8  # the mask must hide at least half of the account number

# ASCII-only decimal literal. ``float()`` alone is too lenient for broker data: it also accepts
# surrounding whitespace, ``1_000``, non-ASCII digits, ``nan`` and ``inf``.
_DECIMAL: Final = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_MISSING: Final = object()
# RFC 3339 date-time with a mandatory offset, ASCII digits only. Alpaca sends up to nanoseconds;
# the fraction is truncated to microseconds (``datetime`` resolution) before parsing. Offset
# minutes are 00..59 here because ``fromisoformat`` silently normalises +00:60 (F-025); offset
# hours of 24 or more are rejected by ``fromisoformat`` itself.
_TIMESTAMP: Final = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?"
    r"(Z|[+-][0-9]{2}:[0-5][0-9])"
)
# Ticker symbols (equities incl. class shares, crypto pairs, OCC option symbols). Validated before
# a symbol is ever echoed in an error message, so broker data cannot inject log lines.
_SYMBOL: Final = re.compile(r"[A-Z0-9][A-Z0-9./-]{0,31}")
_CURRENCY: Final = re.compile(r"[A-Z]{3}")


class BrokerError(RuntimeError):
    """A read-only broker request failed.

    The message names the operation, the exception type and, when known, the HTTP status. It
    never contains credentials, URLs or response bodies. The original exception is deliberately
    not chained (its text can include the request URL), so tracebacks cannot leak it either.
    """

    def __init__(
        self, message: str, *, operation: str | None = None, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.status_code = status_code


class BrokerDataError(BrokerError):
    """The broker returned data that violates the snapshot contract (missing, malformed, NaN)."""


class AccountBlockReason(StrEnum):
    """Explicit reason codes why the account must not be treated as able to trade."""

    STATUS_NOT_ACTIVE = "status_not_active"
    ACCOUNT_BLOCKED = "account_blocked"
    TRADING_BLOCKED = "trading_blocked"
    TRADE_SUSPENDED_BY_USER = "trade_suspended_by_user"
    UNSUPPORTED_CURRENCY = "unsupported_currency"


class ReadOnlyTradingClient(Protocol):
    """The only broker surface this module may use: ``GET`` of three fixed read-only paths.

    ``alpaca.trading.client.TradingClient`` satisfies it structurally (its ``get`` returns the
    decoded JSON body). Obtain clients only from
    :func:`semiedge.safety.broker.create_paper_trading_client`.
    """

    def get(self, path: AccountReadPath) -> object: ...


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """One open position as reported by the broker, numbers parsed from Alpaca's strings.

    ``qty``, ``market_value``, ``cost_basis`` and ``unrealized_pl`` are signed as reported
    (Alpaca reports short quantities as negative). ``unrealized_plpc`` is a fraction
    (``0.05`` = +5 %), as Alpaca reports it.
    """

    symbol: str
    qty: float
    side: PositionSide
    avg_entry_price: float
    market_value: float
    cost_basis: float
    unrealized_pl: float
    unrealized_plpc: float
    current_price: float
    asset_class: str

    def __post_init__(self) -> None:
        if type(self.symbol) is not str or _SYMBOL.fullmatch(self.symbol) is None:
            raise BrokerDataError("position: symbol must be an upper-case ticker symbol")
        where = f"position {self.symbol}"
        _check_text(self.asset_class, f"{where}: asset_class")
        if self.side not in ("long", "short"):
            raise BrokerDataError(f"{where}: side must be 'long' or 'short'")
        for name in (
            "qty",
            "avg_entry_price",
            "market_value",
            "cost_basis",
            "unrealized_pl",
            "unrealized_plpc",
            "current_price",
        ):
            _check_finite(getattr(self, name), f"{where}: {name}")
        if self.avg_entry_price <= 0 or self.current_price <= 0:
            raise BrokerDataError(f"{where}: avg_entry_price and current_price must be positive")
        sign = 1 if self.side == "long" else -1
        for name in ("qty", "market_value", "cost_basis"):
            if getattr(self, name) * sign <= 0:
                raise BrokerDataError(f"{where}: {name} sign contradicts side {self.side!r}")


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Point-in-time, read-only view of the Alpaca PAPER account.

    ``pattern_day_trader`` is ``None`` when the broker does not report it: Alpaca removed the
    field from responses on 2026-07-06 (alpaca-py documents it as deprecated). ``None`` means
    *unknown*, never ``False``. ``trading_blocked``, ``account_blocked`` and
    ``trade_suspended_by_user`` are required; an unknown blocking state fails closed.
    :attr:`block_reasons` lists every explicit reason the account must not be treated as able to
    trade (empty iff :attr:`is_tradable`). ``equity``, ``last_equity``, ``buying_power`` and
    ``long_market_value`` must be non-negative; ``cash`` may be negative (margin).
    Datetimes are timezone-aware UTC; ``positions`` are sorted by symbol.
    """

    account_number_masked: str
    status: str
    currency: str
    equity: float
    last_equity: float
    cash: float
    buying_power: float
    long_market_value: float
    pattern_day_trader: bool | None
    trading_blocked: bool
    account_blocked: bool
    trade_suspended_by_user: bool
    market_is_open: bool
    next_open: datetime
    next_close: datetime
    as_of: datetime
    positions: tuple[PositionSnapshot, ...]

    def __post_init__(self) -> None:
        masked = self.account_number_masked
        if not (
            isinstance(masked, str)
            and len(masked) == len(ACCOUNT_MASK_PREFIX) + VISIBLE_ACCOUNT_CHARS
            and masked.startswith(ACCOUNT_MASK_PREFIX)
        ):
            raise BrokerDataError(
                f"account: account_number_masked must be {ACCOUNT_MASK_PREFIX!r} followed by "
                f"the last {VISIBLE_ACCOUNT_CHARS} characters"
            )
        _check_text(self.status, "account: status")
        _check_text(self.currency, "account: currency")
        if _CURRENCY.fullmatch(self.currency) is None:
            raise BrokerDataError("account: currency must be a three-letter ISO 4217 code")
        for name in ("equity", "last_equity", "cash", "buying_power", "long_market_value"):
            _check_finite(getattr(self, name), f"account: {name}")
        for name in NON_NEGATIVE_ACCOUNT_FIELDS:
            if getattr(self, name) < 0:
                raise BrokerDataError(f"account: {name} must not be negative")
        if self.pattern_day_trader is not None and not isinstance(self.pattern_day_trader, bool):
            raise BrokerDataError("account: pattern_day_trader must be a boolean or None")
        for name in (
            "trading_blocked",
            "account_blocked",
            "trade_suspended_by_user",
            "market_is_open",
        ):
            if not isinstance(getattr(self, name), bool):
                raise BrokerDataError(f"account: {name} must be a boolean")
        self._check_clock()
        self._check_positions()

    def _check_clock(self) -> None:
        for name in ("next_open", "next_close", "as_of"):
            _check_utc(getattr(self, name), f"clock: {name}")
        if not (self.next_open > self.as_of and self.next_close > self.as_of):
            raise BrokerDataError("clock: next_open and next_close must be after the timestamp")
        if self.market_is_open != (self.next_close < self.next_open):
            raise BrokerDataError("clock: is_open contradicts the order of next_open/next_close")

    def _check_positions(self) -> None:
        if not isinstance(self.positions, tuple) or not all(
            isinstance(p, PositionSnapshot) for p in self.positions
        ):
            raise BrokerDataError("positions must be a tuple of PositionSnapshot")
        symbols = [p.symbol for p in self.positions]
        duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
        if duplicates:
            raise BrokerDataError(f"positions: duplicate symbols {duplicates}")
        if symbols != sorted(symbols):
            raise BrokerDataError("positions must be sorted by symbol")

    @property
    def block_reasons(self) -> tuple[AccountBlockReason, ...]:
        """Every reason the account must not trade, in a stable order; empty iff tradable."""
        reasons: list[AccountBlockReason] = []
        if self.status != TRADABLE_ACCOUNT_STATUS:
            reasons.append(AccountBlockReason.STATUS_NOT_ACTIVE)
        if self.account_blocked:
            reasons.append(AccountBlockReason.ACCOUNT_BLOCKED)
        if self.trading_blocked:
            reasons.append(AccountBlockReason.TRADING_BLOCKED)
        if self.trade_suspended_by_user:
            reasons.append(AccountBlockReason.TRADE_SUSPENDED_BY_USER)
        if self.currency != SUPPORTED_CURRENCY:
            reasons.append(AccountBlockReason.UNSUPPORTED_CURRENCY)
        return tuple(reasons)

    @property
    def is_tradable(self) -> bool:
        """True only when the broker reports no blocking condition at all."""
        return not self.block_reasons


def mask_account_number(account_number: str) -> str:
    """Return ``'****'`` plus the last four characters; refuse numbers too short to mask."""
    if type(account_number) is not str or len(account_number) < MIN_ACCOUNT_NUMBER_CHARS:
        raise BrokerDataError(
            f"account: account_number must have at least {MIN_ACCOUNT_NUMBER_CHARS} characters "
            "to be masked safely"
        )
    return ACCOUNT_MASK_PREFIX + account_number[-VISIBLE_ACCOUNT_CHARS:]


def fetch_account_snapshot(client: ReadOnlyTradingClient) -> AccountSnapshot:
    """Read the PAPER account, market clock and open positions into an :class:`AccountSnapshot`.

    Raises:
        SafetyViolation: the client is not bound to the Alpaca PAPER endpoint (checked before any
            request is made).
        BrokerError: a broker request failed (sanitised: no credentials, URLs or bodies).
        BrokerDataError: the broker response is missing, malformed or non-finite.
    """
    _assert_exact_paper_endpoint(client)
    assert_paper_client(client)
    # Raw JSON on purpose: SDK models would coerce malformed values before validation (F-009).
    operation = "GET /account"
    try:
        account = client.get("/account")
        operation = "GET /clock"
        clock = client.get("/clock")
        operation = "GET /positions"
        positions = client.get("/positions")
    except Exception as exc:
        failure = _sanitised_failure(operation, exc)
    else:
        return _parse_snapshot(account, clock, positions)
    # Raised outside the ``except`` block so the original exception (whose text may contain the
    # request URL or response body) is neither the ``__cause__`` nor the ``__context__``.
    raise failure


def _assert_exact_paper_endpoint(client: object) -> None:
    """Type-exact twin of :func:`assert_paper_client`, checked first (defence in depth).

    ``assert_paper_client`` compares ``str()`` of the base URL; a ``str`` subclass with a lying
    ``__str__`` would pass it while the SDK concatenates the real value into request URLs. Here
    only the SDK's own ``BaseURL.TRADING_PAPER`` member or an exact ``str`` is accepted.
    """
    raw = getattr(client, "_base_url", None)
    if raw is BaseURL.TRADING_PAPER:
        return
    if type(raw) is str and raw.rstrip("/") == PAPER_TRADING_BASE_URL:
        return
    raise SafetyViolation(f"Refusing trading client: only {PAPER_TRADING_BASE_URL} is allowed.")


def _parse_snapshot(account: object, clock: object, positions: object) -> AccountSnapshot:
    """Parse the responses; any unexpected parsing exception becomes a sanitised error."""
    try:
        return _build_snapshot(account, clock, positions)
    except BrokerDataError:
        raise
    except Exception as exc:
        failure = BrokerDataError(
            f"account snapshot: broker response could not be parsed ({type(exc).__name__})"
        )
    raise failure  # outside ``except``: the raw exception text is not chained


# ------------------------------------------------------------------ request failures


def _sanitised_failure(operation: str, exc: Exception) -> BrokerError:
    status = _http_status(exc)
    detail = type(exc).__name__ if status is None else f"{type(exc).__name__}, HTTP {status}"
    return BrokerError(
        f"Alpaca paper {operation} request failed ({detail}).",
        operation=operation,
        status_code=status,
    )


def _http_status(exc: Exception) -> int | None:
    """HTTP status of an ``alpaca.common.exceptions.APIError``-like exception, if any."""
    try:
        status = getattr(exc, "status_code", None)
    except Exception:  # a property that fails while reading the status
        return None
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        return status
    return None


# ------------------------------------------------------------------ parsing


def _build_snapshot(
    raw_account: object, raw_clock: object, raw_positions: object
) -> AccountSnapshot:
    account = _json_object(raw_account, "account")
    clock = _json_object(raw_clock, "clock")
    return AccountSnapshot(
        account_number_masked=mask_account_number(_text(account, "account_number", "account")),
        status=_text(account, "status", "account"),
        currency=_text(account, "currency", "account"),
        equity=_decimal(account, "equity", "account"),
        last_equity=_decimal(account, "last_equity", "account"),
        cash=_decimal(account, "cash", "account"),
        buying_power=_decimal(account, "buying_power", "account"),
        long_market_value=_decimal(account, "long_market_value", "account"),
        pattern_day_trader=_optional_flag(account, "pattern_day_trader", "account"),
        trading_blocked=_flag(account, "trading_blocked", "account"),
        account_blocked=_flag(account, "account_blocked", "account"),
        trade_suspended_by_user=_flag(account, "trade_suspended_by_user", "account"),
        market_is_open=_flag(clock, "is_open", "clock"),
        next_open=_utc_datetime(clock, "next_open", "clock"),
        next_close=_utc_datetime(clock, "next_close", "clock"),
        as_of=_utc_datetime(clock, "timestamp", "clock"),
        positions=_parse_positions(raw_positions),
    )


def _parse_positions(raw: object) -> tuple[PositionSnapshot, ...]:
    if type(raw) is not list:  # exact: a decoded JSON array is a plain list
        raise BrokerDataError(f"positions: expected a list, got {type(raw).__name__}")
    parsed = [_parse_position(item, index) for index, item in enumerate(raw)]
    return tuple(sorted(parsed, key=lambda p: p.symbol))


def _parse_position(raw: object, index: int) -> PositionSnapshot:
    position = _json_object(raw, f"positions[{index}]")
    symbol = _text(position, "symbol", f"positions[{index}]")
    if _SYMBOL.fullmatch(symbol) is None:
        raise BrokerDataError(f"positions[{index}]: 'symbol' is not a valid ticker symbol")
    where = f"position {symbol}"
    return PositionSnapshot(
        symbol=symbol,
        qty=_decimal(position, "qty", where),
        side=_side(position, where),
        avg_entry_price=_decimal(position, "avg_entry_price", where),
        market_value=_decimal(position, "market_value", where),
        cost_basis=_decimal(position, "cost_basis", where),
        unrealized_pl=_decimal(position, "unrealized_pl", where),
        unrealized_plpc=_decimal(position, "unrealized_plpc", where),
        current_price=_decimal(position, "current_price", where),
        asset_class=_text(position, "asset_class", where),
    )


def _json_object(raw: object, where: str) -> dict[str, object]:
    """The decoded JSON object itself: exact ``dict`` (no SDK model, no mapping subclass)."""
    if type(raw) is not dict:
        raise BrokerDataError(f"{where}: expected a JSON object, got {type(raw).__name__}")
    return raw


def _field(obj: dict[str, object], name: str, where: str) -> object:
    value = obj.get(name, _MISSING)
    if value is _MISSING or value is None:
        raise BrokerDataError(f"{where}: required field {name!r} is missing")
    return value


def _decimal(obj: dict[str, object], name: str, where: str) -> float:
    raw = _field(obj, name, where)
    # exact types: ``isinstance`` alone admits bool and str subclasses with a lying __float__
    if type(raw) is str:
        if _DECIMAL.fullmatch(raw) is None:
            raise BrokerDataError(f"{where}: {name!r} is not a finite decimal number")
        number: str | int | float = raw
    elif type(raw) is int or type(raw) is float:
        number = raw
    else:
        raise BrokerDataError(
            f"{where}: {name!r} must be a decimal string, got {type(raw).__name__}"
        )
    try:
        value = float(number)
    except OverflowError:  # an int beyond the float range
        raise BrokerDataError(f"{where}: {name!r} is not finite") from None
    if not math.isfinite(value):
        raise BrokerDataError(f"{where}: {name!r} is not finite")
    return value


def _text(obj: dict[str, object], name: str, where: str) -> str:
    raw = _field(obj, name, where)
    if type(raw) is not str:  # exact: a str subclass could override comparison or formatting
        raise BrokerDataError(f"{where}: {name!r} must be a string, got {type(raw).__name__}")
    _check_text(raw, f"{where}: {name}")
    return raw


def _side(obj: dict[str, object], where: str) -> PositionSide:
    side = _text(obj, "side", where)
    if side == "long":
        return "long"
    if side == "short":
        return "short"
    raise BrokerDataError(f"{where}: 'side' must be 'long' or 'short'")


def _flag(obj: dict[str, object], name: str, where: str) -> bool:
    raw = _field(obj, name, where)
    if type(raw) is not bool:  # a JSON boolean only: never 0, 1, "false" or "true"
        raise BrokerDataError(f"{where}: {name!r} must be a boolean, got {type(raw).__name__}")
    return raw


def _optional_flag(obj: dict[str, object], name: str, where: str) -> bool | None:
    raw = obj.get(name)  # absent or null: not reported (unknown), never False
    if raw is None:
        return None
    if type(raw) is not bool:
        raise BrokerDataError(f"{where}: {name!r} must be a boolean, got {type(raw).__name__}")
    return raw


def _utc_datetime(obj: dict[str, object], name: str, where: str) -> datetime:
    raw = _field(obj, name, where)
    if type(raw) is not str:  # a JSON number must not become an epoch timestamp
        raise BrokerDataError(
            f"{where}: {name!r} must be an RFC 3339 timestamp string, got {type(raw).__name__}"
        )
    match = _TIMESTAMP.fullmatch(raw)
    if match is None:
        raise BrokerDataError(f"{where}: {name!r} is not an RFC 3339 timestamp with an offset")
    seconds, fraction, offset = match.groups()
    micros = (fraction or "").ljust(6, "0")[:6]
    try:
        parsed: datetime | None = datetime.fromisoformat(
            f"{seconds}.{micros}{'+00:00' if offset == 'Z' else offset}"
        )
    except ValueError:  # e.g. month 13, hour 25, an offset of 24 h or more
        parsed = None
    if parsed is None:
        raise BrokerDataError(f"{where}: {name!r} is not a valid RFC 3339 timestamp")
    try:
        converted: datetime | None = parsed.astimezone(UTC)
    except OverflowError:
        converted = None
    if converted is None:
        raise BrokerDataError(f"{where}: {name!r} is outside the representable range")
    return converted


# ------------------------------------------------------------------ invariants


def _check_text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise BrokerDataError(f"{label} must be a non-empty string without surrounding spaces")


def _check_finite(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrokerDataError(f"{label} must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise BrokerDataError(f"{label} must be finite")


def _check_utc(value: object, label: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise BrokerDataError(f"{label} must be a timezone-aware UTC datetime")
