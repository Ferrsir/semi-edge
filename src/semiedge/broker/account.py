"""Read-only Alpaca PAPER account snapshot (issue #13).

:func:`fetch_account_snapshot` first verifies that the client is bound to the PAPER endpoint
(a type-exact base-URL check, then :func:`semiedge.safety.broker.assert_paper_client`) and then
makes exactly three read requests: ``get_account``, ``get_clock`` and ``get_all_positions``.
Nothing here can change broker state. The client parameter is typed with
:class:`ReadOnlyTradingClient`, a protocol that exposes only those three methods, so mypy
rejects any other broker call; ``tests/safety/test_broker_readonly.py`` proves the same at
runtime with a client that refuses every other attribute.

Fail closed. Alpaca serialises money and quantities as decimal strings. Every field the snapshot
needs is parsed strictly: a missing, mistyped, unparseable or non-finite value raises
:class:`BrokerDataError` and is never replaced by a default such as ``0``. Error messages name the
field and the problem but never echo raw values, credentials or URLs.

The three requests are not atomic: account, clock and positions are read at slightly different
instants, so account totals (e.g. ``long_market_value``) need not equal the sum over
``positions``. ``as_of`` is the broker clock timestamp, converted to UTC.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, StrEnum
from typing import Final, Literal, Protocol

from alpaca.common.enums import BaseURL

from semiedge.safety.broker import PAPER_TRADING_BASE_URL, assert_paper_client
from semiedge.safety.modes import SafetyViolation

PositionSide = Literal["long", "short"]

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
_FAILED: Final = object()
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
    """The only broker surface this module may use.

    ``alpaca.trading.client.TradingClient`` satisfies it structurally. Obtain clients only from
    :func:`semiedge.safety.broker.create_paper_trading_client`.
    """

    def get_account(self) -> object: ...

    def get_clock(self) -> object: ...

    def get_all_positions(self) -> object: ...


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
    operation = "get_account"
    try:
        account = client.get_account()
        operation = "get_clock"
        clock = client.get_clock()
        operation = "get_all_positions"
        positions = client.get_all_positions()
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


def _build_snapshot(account: object, clock: object, positions: object) -> AccountSnapshot:
    _require_model(account, "account")
    _require_model(clock, "clock")
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
        positions=_parse_positions(positions),
    )


def _parse_positions(raw: object) -> tuple[PositionSnapshot, ...]:
    if not isinstance(raw, (list, tuple)):
        raise BrokerDataError(f"positions: expected a list, got {type(raw).__name__}")
    parsed = [_parse_position(item, index) for index, item in enumerate(raw)]
    return tuple(sorted(parsed, key=lambda p: p.symbol))


def _parse_position(raw: object, index: int) -> PositionSnapshot:
    _require_model(raw, f"positions[{index}]")
    symbol = _text(raw, "symbol", f"positions[{index}]")
    if _SYMBOL.fullmatch(symbol) is None:
        raise BrokerDataError(f"positions[{index}]: 'symbol' is not a valid ticker symbol")
    where = f"position {symbol}"
    return PositionSnapshot(
        symbol=symbol,
        qty=_decimal(raw, "qty", where),
        side=_side(raw, where),
        avg_entry_price=_decimal(raw, "avg_entry_price", where),
        market_value=_decimal(raw, "market_value", where),
        cost_basis=_decimal(raw, "cost_basis", where),
        unrealized_pl=_decimal(raw, "unrealized_pl", where),
        unrealized_plpc=_decimal(raw, "unrealized_plpc", where),
        current_price=_decimal(raw, "current_price", where),
        asset_class=_text(raw, "asset_class", where),
    )


def _require_model(obj: object, where: str) -> None:
    if obj is None or isinstance(obj, Mapping):
        raise BrokerDataError(
            f"{where}: expected an Alpaca model object, got {type(obj).__name__} "
            "(raw-data clients are not supported)"
        )


def _field(obj: object, name: str, where: str) -> object:
    try:
        value = getattr(obj, name, _MISSING)
    except Exception:  # a failing property: never let its message (maybe a body) escape
        value = _FAILED
    if value is _FAILED:
        raise BrokerDataError(f"{where}: required field {name!r} could not be read")
    if value is _MISSING or value is None:
        raise BrokerDataError(f"{where}: required field {name!r} is missing")
    return value


def _decimal(obj: object, name: str, where: str) -> float:
    raw = _field(obj, name, where)
    # exact types (isinstance alone admits bool, str subclasses with a lying __float__, numpy)
    if not isinstance(raw, (str, int, float)) or type(raw) not in (str, int, float):
        raise BrokerDataError(
            f"{where}: {name!r} must be a decimal string, got {type(raw).__name__}"
        )
    if isinstance(raw, str) and _DECIMAL.fullmatch(raw) is None:
        raise BrokerDataError(f"{where}: {name!r} is not a finite decimal number")
    try:
        value = float(raw)
    except OverflowError:  # an int beyond the float range
        raise BrokerDataError(f"{where}: {name!r} is not finite") from None
    if not math.isfinite(value):
        raise BrokerDataError(f"{where}: {name!r} is not finite")
    return value


def _text(obj: object, name: str, where: str) -> str:
    raw = _field(obj, name, where)
    if isinstance(raw, Enum):  # alpaca enums mix in str; str() would give "Enum.MEMBER"
        raw = raw.value
    if type(raw) is not str:  # exact: a str subclass could override comparison or formatting
        raise BrokerDataError(f"{where}: {name!r} must be a string, got {type(raw).__name__}")
    _check_text(raw, f"{where}: {name}")
    return raw


def _side(obj: object, where: str) -> PositionSide:
    side = _text(obj, "side", where)
    if side == "long":
        return "long"
    if side == "short":
        return "short"
    raise BrokerDataError(f"{where}: 'side' must be 'long' or 'short'")


def _flag(obj: object, name: str, where: str) -> bool:
    raw = _field(obj, name, where)
    if not isinstance(raw, bool):
        raise BrokerDataError(f"{where}: {name!r} must be a boolean, got {type(raw).__name__}")
    return raw


def _optional_flag(obj: object, name: str, where: str) -> bool | None:
    raw = getattr(obj, name, None)
    if raw is None:
        return None
    if not isinstance(raw, bool):
        raise BrokerDataError(f"{where}: {name!r} must be a boolean, got {type(raw).__name__}")
    return raw


def _utc_datetime(obj: object, name: str, where: str) -> datetime:
    raw = _field(obj, name, where)
    if not isinstance(raw, datetime):
        raise BrokerDataError(f"{where}: {name!r} must be a datetime, got {type(raw).__name__}")
    if raw.utcoffset() is None:
        raise BrokerDataError(f"{where}: {name!r} is timezone-naive")
    try:
        converted: datetime | None = raw.astimezone(UTC)
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
