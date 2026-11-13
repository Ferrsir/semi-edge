"""Shared row codecs and the one ``StateReader`` implementation (spec §5.1, F-037; issue #21).

``StateStore`` (writer) and ``ReadOnlyStateStore`` (dashboard/health) both inherit
:class:`StateReaderBase`, so every ``StateReader`` method has literally the same signature, SQL
and row decoding in both: there is nothing to keep in sync by hand.

Rows read back are decoded strictly: a value that cannot have been written by the store
(unknown status, naive timestamp, wrong type) raises :class:`StateStoreError` instead of being
passed on as a default (fail closed).
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime
from typing import Any, Final, Literal, cast, get_args

from semiedge.risk.types import HaltKind, OrderSide
from semiedge.state.migrations import LATEST_SCHEMA_VERSION
from semiedge.state.models import (
    CorporateActionAdjustment,
    Discrepancy,
    DiscrepancyKind,
    FillRecord,
    FillSource,
    FillStatus,
    HaltRecord,
    OrderIntent,
    OrderKind,
    OrderRecord,
    OrderStatus,
    PositionRecord,
    ReconciliationReport,
)

__all__ = ["StateReaderBase", "StateStoreError"]

ORDER_KINDS: Final[frozenset[str]] = frozenset(get_args(OrderKind))
ORDER_STATUSES: Final[frozenset[str]] = frozenset(get_args(OrderStatus))
DATA_SOURCES: Final = ("real", "synthetic")


class StateStoreError(RuntimeError):
    """The state database refused an operation or holds data the store cannot trust."""


# ------------------------------------------------------------------ encoding


def ts_text(value: object, field: str) -> str:
    """ISO-8601 UTC with offset and fixed microsecond precision (so text order == time order)."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StateStoreError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def date_text(value: object, field: str) -> str:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise StateStoreError(f"{field} must be a date")
    return value.isoformat()


def dumps(value: object) -> str:
    """The one ``*_json`` encoding (§5.2): sorted keys, no NaN/inf."""
    try:
        return json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise StateStoreError(f"value is not strict JSON: {exc}") from None


def is_int(value: object) -> bool:
    return type(value) is int


def is_finite(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def text(value: object, field: str, *, max_len: int = 10_000) -> str:
    if type(value) is not str or not value or len(value) > max_len:
        raise StateStoreError(f"{field} must be a non-empty string of at most {max_len} chars")
    return value


# ------------------------------------------------------------------ decoding


def _parse_ts(value: object, field: str) -> datetime:
    if type(value) is not str:
        raise StateStoreError(f"stored {field} is not text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise StateStoreError(f"stored {field} is not an ISO timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StateStoreError(f"stored {field} has no UTC offset")
    return parsed.astimezone(UTC)


def _parse_opt_ts(value: object, field: str) -> datetime | None:
    return None if value is None else _parse_ts(value, field)


def _parse_date(value: object, field: str) -> date:
    if type(value) is not str:
        raise StateStoreError(f"stored {field} is not text")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise StateStoreError(f"stored {field} is not an ISO date") from None


def _int(value: object, field: str) -> int:
    if type(value) is not int:
        raise StateStoreError(f"stored {field} is not an integer")
    return value


def _float(value: object, field: str) -> float:
    if not is_finite(value):
        raise StateStoreError(f"stored {field} is not a finite number")
    return float(cast(float, value))


def _opt_float(value: object, field: str) -> float | None:
    return None if value is None else _float(value, field)


def _str(value: object, field: str) -> str:
    if type(value) is not str:
        raise StateStoreError(f"stored {field} is not text")
    return value


def _opt_str(value: object, field: str) -> str | None:
    return None if value is None else _str(value, field)


def _choice(value: object, allowed: Collection[str], field: str) -> str:
    if type(value) is not str or value not in allowed:
        raise StateStoreError(f"stored {field} {value!r} is not a known value")
    return value


ORDER_COLUMNS: Final = (
    "client_order_id, kind, symbol, side, qty, order_type, time_in_force, order_class, "
    "limit_price, stop_price, decision_session, valid_until, risk_decision_id, "
    "parent_client_order_id, created_at, status, broker_order_id, filled_qty, "
    "filled_avg_price, legs_json, submitted_at, updated_at, last_error"
)
FILL_COLUMNS: Final = (
    "fill_key, client_order_id, ts, qty, price, source, status, cum_qty, superseded_at"
)
POSITION_COLUMNS: Final = (
    "symbol, qty, avg_entry_price, entry_session, entry_client_order_id, entry_reference, atr, "
    "stop, target, split_ratio, status, opened_at, closed_at, updated_at"
)
HALT_COLUMNS: Final = "id, kind, reason, created_at, created_by, cleared_at, cleared_by"
ADJUSTMENT_COLUMNS: Final = (
    "symbol, ex_date, event_id, new_shares, old_shares, qty_before, qty_after, applied_at"
)


def decode_order(row: Sequence[Any]) -> OrderRecord:
    (
        cid,
        kind,
        symbol,
        side,
        qty,
        order_type,
        tif,
        order_class,
        limit_price,
        stop_price,
        session,
        valid_until,
        decision_id,
        parent,
        created_at,
        status,
        broker_id,
        filled_qty,
        avg_price,
        legs_json,
        submitted_at,
        updated_at,
        last_error,
    ) = row
    intent = OrderIntent(
        client_order_id=_str(cid, "client_order_id"),
        kind=cast(OrderKind, _choice(kind, ORDER_KINDS, "kind")),
        symbol=_str(symbol, "symbol"),
        side=OrderSide(_choice(side, ("buy", "sell"), "side")),
        qty=_int(qty, "qty"),
        order_type=cast(
            Literal["market", "limit"], _choice(order_type, ("market", "limit"), "order_type")
        ),
        time_in_force=cast(
            Literal["opg", "day", "gtc"], _choice(tif, ("opg", "day", "gtc"), "time_in_force")
        ),
        order_class=cast(
            Literal["simple", "oco"], _choice(order_class, ("simple", "oco"), "order_class")
        ),
        limit_price=_opt_float(limit_price, "limit_price"),
        stop_price=_opt_float(stop_price, "stop_price"),
        decision_session=_parse_date(session, "decision_session"),
        valid_until=_parse_ts(valid_until, "valid_until"),
        risk_decision_id=None if decision_id is None else _int(decision_id, "risk_decision_id"),
        parent_client_order_id=_opt_str(parent, "parent_client_order_id"),
        created_at=_parse_ts(created_at, "created_at"),
    )
    return OrderRecord(
        intent=intent,
        status=cast(OrderStatus, _choice(status, ORDER_STATUSES, "status")),
        broker_order_id=_opt_str(broker_id, "broker_order_id"),
        filled_qty=_int(filled_qty, "filled_qty"),
        filled_avg_price=_opt_float(avg_price, "filled_avg_price"),
        legs_json=_str(legs_json, "legs_json"),
        submitted_at=_parse_opt_ts(submitted_at, "submitted_at"),
        updated_at=_parse_ts(updated_at, "updated_at"),
        last_error=_opt_str(last_error, "last_error"),
    )


def decode_fill(row: Sequence[Any]) -> FillRecord:
    key, cid, ts, qty, price, source, status, cum_qty, superseded_at = row
    return FillRecord(
        fill_key=_str(key, "fill_key"),
        client_order_id=_str(cid, "client_order_id"),
        ts=_parse_ts(ts, "ts"),
        qty=_int(qty, "qty"),
        price=_float(price, "price"),
        source=cast(FillSource, _choice(source, ("activity", "cumulative"), "source")),
        status=cast(
            FillStatus, _choice(status, ("final", "provisional", "superseded"), "fill status")
        ),
        cum_qty=_int(cum_qty, "cum_qty"),
        superseded_at=_parse_opt_ts(superseded_at, "superseded_at"),
    )


def decode_position(row: Sequence[Any]) -> PositionRecord:
    (
        symbol,
        qty,
        avg_price,
        entry_session,
        entry_cid,
        entry_reference,
        atr,
        stop,
        target,
        split_ratio,
        status,
        opened_at,
        closed_at,
        updated_at,
    ) = row
    return PositionRecord(
        symbol=_str(symbol, "symbol"),
        qty=_int(qty, "qty"),
        avg_entry_price=_float(avg_price, "avg_entry_price"),
        entry_session=_parse_date(entry_session, "entry_session"),
        entry_client_order_id=_str(entry_cid, "entry_client_order_id"),
        entry_reference=_float(entry_reference, "entry_reference"),
        atr=_float(atr, "atr"),
        stop=_float(stop, "stop"),
        target=_float(target, "target"),
        split_ratio=_float(split_ratio, "split_ratio"),
        status=cast(
            Literal["open", "exiting", "closed"],
            _choice(status, ("open", "exiting", "closed"), "position status"),
        ),
        opened_at=_parse_ts(opened_at, "opened_at"),
        closed_at=_parse_opt_ts(closed_at, "closed_at"),
        updated_at=_parse_ts(updated_at, "updated_at"),
    )


def decode_halt(row: Sequence[Any]) -> HaltRecord:
    halt_id, kind, reason, created_at, created_by, cleared_at, cleared_by = row
    return HaltRecord(
        id=_int(halt_id, "halt id"),
        kind=HaltKind(_choice(kind, {k.value for k in HaltKind}, "halt kind")),
        reason=_str(reason, "reason"),
        created_at=_parse_ts(created_at, "created_at"),
        created_by=_str(created_by, "created_by"),
        cleared_at=_parse_opt_ts(cleared_at, "cleared_at"),
        cleared_by=_opt_str(cleared_by, "cleared_by"),
    )


def decode_adjustment(row: Sequence[Any]) -> CorporateActionAdjustment:
    symbol, ex_date, event_id, new_shares, old_shares, qty_before, qty_after, applied_at = row
    return CorporateActionAdjustment(
        symbol=_str(symbol, "symbol"),
        ex_date=_parse_date(ex_date, "ex_date"),
        event_id=_str(event_id, "event_id"),
        new_shares=_int(new_shares, "new_shares"),
        old_shares=_int(old_shares, "old_shares"),
        qty_before=_int(qty_before, "qty_before"),
        qty_after=_int(qty_after, "qty_after"),
        applied_at=_parse_ts(applied_at, "applied_at"),
    )


def encode_discrepancies(discrepancies: Sequence[Discrepancy]) -> str:
    items: list[dict[str, object]] = []
    for d in discrepancies:
        if not isinstance(d, Discrepancy) or not isinstance(d.kind, DiscrepancyKind):
            raise StateStoreError("discrepancies must be Discrepancy records")
        if d.resolution not in ("applied", "halt", "action_required"):
            raise StateStoreError(f"unknown discrepancy resolution {d.resolution!r}")
        for name in ("symbol", "client_order_id"):
            value = getattr(d, name)
            if value is not None and type(value) is not str:
                raise StateStoreError(f"discrepancy {name} must be a string or None")
        if type(d.local) is not str or type(d.broker) is not str:
            raise StateStoreError("discrepancy local/broker must be strings")
        items.append(
            {
                "kind": d.kind.value,
                "symbol": d.symbol,
                "client_order_id": d.client_order_id,
                "local": d.local,
                "broker": d.broker,
                "resolution": d.resolution,
            }
        )
    return dumps(items)


def decode_reconciliation(row: Sequence[Any]) -> ReconciliationReport:
    started_at, finished_at, outcome, discrepancies_json = row
    try:
        items = json.loads(_str(discrepancies_json, "discrepancies_json"))
    except json.JSONDecodeError:
        raise StateStoreError("stored discrepancies_json is not JSON") from None
    if not isinstance(items, list):
        raise StateStoreError("stored discrepancies_json is not a list")
    discrepancies: list[Discrepancy] = []
    for item in items:
        if not isinstance(item, dict):
            raise StateStoreError("stored discrepancy is not an object")
        discrepancies.append(
            Discrepancy(
                kind=DiscrepancyKind(
                    _choice(item.get("kind"), {k.value for k in DiscrepancyKind}, "kind")
                ),
                symbol=_opt_str(item.get("symbol"), "symbol"),
                client_order_id=_opt_str(item.get("client_order_id"), "client_order_id"),
                local=_str(item.get("local"), "local"),
                broker=_str(item.get("broker"), "broker"),
                resolution=cast(
                    Literal["applied", "halt", "action_required"],
                    _choice(
                        item.get("resolution"), ("applied", "halt", "action_required"), "resolution"
                    ),
                ),
            )
        )
    return ReconciliationReport(
        started_at=_parse_ts(started_at, "started_at"),
        finished_at=_parse_ts(finished_at, "finished_at"),
        outcome=cast(
            Literal["clean", "resolved", "halted", "error"],
            _choice(outcome, ("clean", "resolved", "halted", "error"), "outcome"),
        ),
        discrepancies=tuple(discrepancies),
    )


# ------------------------------------------------------------------ schema / identity checks


def read_identity(conn: sqlite3.Connection) -> tuple[str, Literal["real", "synthetic"]]:
    """Check the schema version is one this binary knows and return (store_id, data_source)."""
    try:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_version")]
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error as exc:
        raise StateStoreError(f"not a SEMI-EDGE state database: {exc}") from None
    if not versions or any(type(v) is not int for v in versions):
        raise StateStoreError("state database has no valid schema_version")
    if max(versions) > LATEST_SCHEMA_VERSION:
        raise StateStoreError(
            f"state database schema_version {max(versions)} is newer than this binary "
            f"({LATEST_SCHEMA_VERSION}); refusing to use it"
        )
    store_id = meta.get("store_id")
    source = meta.get("data_source")
    if type(store_id) is not str or len(store_id) != 32:
        raise StateStoreError("state database meta.store_id is missing or malformed")
    if source not in DATA_SOURCES:
        raise StateStoreError("state database meta.data_source is missing or malformed")
    return store_id, cast(Literal["real", "synthetic"], source)


# ------------------------------------------------------------------ argument validation (reads)


def _check_limit(limit: object) -> int | None:
    if limit is None:
        return None
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be None or an int > 0")
    return limit


def _check_values(values: object, allowed: frozenset[str], name: str) -> list[str] | None:
    if values is None:
        return None
    if isinstance(values, str | bytes) or not isinstance(values, Collection):
        raise ValueError(f"{name} must be a collection of values or None")
    checked = list(values)
    unknown = [v for v in checked if type(v) is not str or v not in allowed]
    if unknown:
        raise ValueError(f"unknown {name}: {sorted(map(repr, unknown))}")
    return sorted(set(checked))


def _check_opt_str(value: object, name: str) -> str | None:
    if value is not None and type(value) is not str:
        raise ValueError(f"{name} must be a string or None")
    return value


class StateReaderBase:
    """The single implementation of :class:`semiedge.state.models.StateReader`."""

    _conn: sqlite3.Connection
    _store_id: str
    _data_source: Literal["real", "synthetic"]

    def _connection(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self, "_conn", None)
        if conn is None:
            raise StateStoreError("state store is closed")
        return conn

    @property
    def store_id(self) -> str:
        self._connection()
        return self._store_id

    def data_source(self) -> Literal["real", "synthetic"]:
        self._connection()
        return self._data_source

    def orders(
        self,
        *,
        statuses: Collection[OrderStatus] | None = None,
        symbol: str | None = None,
        kinds: Collection[OrderKind] | None = None,
        limit: int | None = None,
    ) -> list[OrderRecord]:
        status_list = _check_values(statuses, ORDER_STATUSES, "statuses")
        kind_list = _check_values(kinds, ORDER_KINDS, "kinds")
        sym = _check_opt_str(symbol, "symbol")
        lim = _check_limit(limit)
        if status_list == [] or kind_list == []:
            return []
        where: list[str] = []
        params: list[object] = []
        if status_list is not None:
            where.append(f"status IN ({', '.join('?' * len(status_list))})")
            params += status_list
        if kind_list is not None:
            where.append(f"kind IN ({', '.join('?' * len(kind_list))})")
            params += kind_list
        if sym is not None:
            where.append("symbol = ?")
            params.append(sym)
        sql = f"SELECT {ORDER_COLUMNS} FROM orders"  # noqa: S608 - constant columns, bound params
        if where:
            sql += " WHERE " + " AND ".join(where)
        if lim is None:
            sql += " ORDER BY created_at ASC, client_order_id ASC"
        else:
            sql += " ORDER BY created_at DESC, client_order_id DESC LIMIT ?"
            params.append(lim)
        return [decode_order(row) for row in self._connection().execute(sql, params)]

    def fills(
        self,
        *,
        client_order_id: str | None = None,
        include_superseded: bool = False,
        limit: int | None = None,
    ) -> list[FillRecord]:
        cid = _check_opt_str(client_order_id, "client_order_id")
        if type(include_superseded) is not bool:
            raise ValueError("include_superseded must be a bool")
        lim = _check_limit(limit)
        where: list[str] = []
        params: list[object] = []
        if cid is not None:
            where.append("client_order_id = ?")
            params.append(cid)
        if not include_superseded:
            where.append("status <> 'superseded'")
        sql = f"SELECT {FILL_COLUMNS} FROM fills"  # noqa: S608 - constant columns, bound params
        if where:
            sql += " WHERE " + " AND ".join(where)
        if lim is None:
            sql += " ORDER BY ts ASC, fill_key ASC"
        else:
            sql += " ORDER BY ts DESC, fill_key DESC LIMIT ?"
            params.append(lim)
        return [decode_fill(row) for row in self._connection().execute(sql, params)]

    def positions(self, *, include_closed: bool = False) -> tuple[PositionRecord, ...]:
        if type(include_closed) is not bool:
            raise ValueError("include_closed must be a bool")
        sql = f"SELECT {POSITION_COLUMNS} FROM positions"  # noqa: S608 - constant columns
        if not include_closed:
            sql += " WHERE status <> 'closed'"
        sql += " ORDER BY symbol"
        return tuple(decode_position(row) for row in self._connection().execute(sql))

    def active_halts(self) -> tuple[HaltRecord, ...]:
        sql = f"SELECT {HALT_COLUMNS} FROM halts WHERE cleared_at IS NULL ORDER BY id"  # noqa: S608
        return tuple(decode_halt(row) for row in self._connection().execute(sql))

    def corporate_action_adjustments(
        self, *, symbol: str | None = None
    ) -> tuple[CorporateActionAdjustment, ...]:
        sym = _check_opt_str(symbol, "symbol")
        sql = f"SELECT {ADJUSTMENT_COLUMNS} FROM corporate_action_adjustments"  # noqa: S608
        params: list[object] = []
        if sym is not None:
            sql += " WHERE symbol = ?"
            params.append(sym)
        sql += " ORDER BY symbol, ex_date, event_id"
        return tuple(decode_adjustment(row) for row in self._connection().execute(sql, params))
