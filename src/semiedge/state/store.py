"""The SQLite state store: the single writer (spec phase2-4-interfaces.md §5; issue #21 part 1).

One process writes (the engine; the CLI when the engine is stopped), guarded by an OS-level lock
on ``{db_dir}/engine.lock`` (§5.3). Every public method runs in its own transaction unless it is
called inside :meth:`StateStore.transaction`; nested calls join the outer transaction through a
SAVEPOINT, so a failing call never leaves partial rows behind even when the caller catches the
error.

Fail closed: invalid input raises :class:`StateStoreError` (or ``ValueError`` for the read
filters, §5.1) and writes nothing. Errors that mean the local state can no longer be trusted
(an illegal order transition, a ``filled_qty`` decrease, fills above the broker cumulative, a
changed broker order id) additionally record a ``RECONCILIATION`` halt (actor ``engine``) once
the outermost transaction ends, even when the caller swallows the exception (§5.3, §6.4). The
halt write is idempotent per kind, so callers that also set the halt (§5.1 "caller sets a
RECONCILIATION halt") do not create a second row.

Nothing here reads the wall clock: the constructor and every mutating method take an
explicit ``now`` (§0.3, §18). Nothing here talks to a broker.
"""

from __future__ import annotations

import itertools
import json
import math
import re
import sqlite3
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import Any, Final, Literal, cast
from zoneinfo import ZoneInfo

from semiedge.reporting.signal_report import evaluation_to_dict
from semiedge.risk.sizing import SizingResult
from semiedge.risk.types import EntryProposal, HaltKind, OrderSide, RiskCheck, RiskDecision
from semiedge.safety.modes import TradingMode
from semiedge.state._lock import WriterLock
from semiedge.state._reader import (
    DATA_SOURCES,
    FILL_COLUMNS,
    ORDER_COLUMNS,
    ORDER_KINDS,
    ORDER_STATUSES,
    POSITION_COLUMNS,
    StateReaderBase,
    StateStoreError,
    date_text,
    decode_fill,
    decode_order,
    decode_position,
    decode_reconciliation,
    dumps,
    encode_discrepancies,
    is_finite,
    is_int,
    read_identity,
    text,
    ts_text,
)
from semiedge.state.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS, split_statements
from semiedge.state.models import (
    TERMINAL_STATUSES,
    BrokerFillInput,
    EquitySnapshot,
    FillIngestResult,
    FillRecord,
    Notification,
    NotificationCategory,
    OrderIntent,
    OrderKind,
    OrderRecord,
    OrderStatus,
    PositionRecord,
    ReconciliationReport,
)
from semiedge.state.order_status import (
    ALLOWED,
    RECOVERY_EDGE,
    SENT_NON_TERMINAL,
    UNSENT_STATUSES,
)
from semiedge.strategy.base import SignalEvaluation

__all__ = ["LOCK_FILE_NAME", "RUN_KINDS", "StateStore", "StateStoreError"]

LOCK_FILE_NAME: Final = "engine.lock"
RUN_KINDS: Final = frozenset({"engine", "post_close", "pre_open", "monitor", "reconcile"})
RUN_FINAL_STATUSES: Final = frozenset({"ok", "failed", "aborted", "skipped"})
# Same pattern as semiedge.config.models.UniverseMember.symbol.
SYMBOL_PATTERN: Final = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
CLIENT_ORDER_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9._:\-]{1,128}")  # Alpaca limit is 128
_ACTOR_NAME: Final = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_NEW_YORK: Final = ZoneInfo("America/New_York")
_ENGINE: Final = "engine"
_ENGINE_CLEARABLE: Final = frozenset({HaltKind.DAILY_LOSS, HaltKind.DATA})
_MAX_REASON: Final = 2000
RECOVERED_UNSENT_EVENT: Final = "recovered_unsent"


@dataclass(frozen=True, slots=True)
class _PendingHalt:
    reason: str
    now: str


def _actor(actor: object) -> str:
    """``engine`` or ``cli:<name>`` (the writer's actors; the dashboard uses halt_control)."""
    if actor == _ENGINE:
        return _ENGINE
    if type(actor) is str and actor.startswith("cli:") and _ACTOR_NAME.fullmatch(actor[4:]):
        return actor
    raise StateStoreError(f"actor must be 'engine' or 'cli:<name>', got {actor!r}")


def _symbol(value: object) -> str:
    if type(value) is not str or SYMBOL_PATTERN.fullmatch(value) is None:
        raise StateStoreError(f"invalid symbol {value!r}")
    return value


def _client_id(value: object) -> str:
    if type(value) is not str or CLIENT_ORDER_ID_PATTERN.fullmatch(value) is None:
        raise StateStoreError(f"invalid client_order_id {value!r}")
    return value


def _positive_int(value: object, field: str) -> int:
    if not is_int(value) or cast(int, value) <= 0:
        raise StateStoreError(f"{field} must be an int > 0")
    return cast(int, value)


def _positive_price(value: object, field: str) -> float:
    if not is_finite(value) or cast(float, value) <= 0:
        raise StateStoreError(f"{field} must be a finite number > 0")
    return float(cast(float, value))


def _finite(value: object, field: str) -> float:
    if not is_finite(value):
        raise StateStoreError(f"{field} must be a finite number")
    return float(cast(float, value))


def _opt_price(value: object, field: str) -> float | None:
    return None if value is None else _positive_price(value, field)


def _jsonable(value: object) -> object:
    """Strict conversion for ``detail_json`` (datetimes as UTC ISO, enums as values)."""
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, float) and not math.isfinite(value):
        raise StateStoreError("detail values must be finite")
    if value is None or isinstance(value, bool | str | int | float):
        result: object = value
    elif isinstance(value, datetime):
        result = ts_text(value, "detail timestamp")
    elif isinstance(value, date):
        result = value.isoformat()
    elif isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise StateStoreError("detail keys must be strings")
        result = {key: _jsonable(item) for key, item in value.items()}
    elif isinstance(value, list | tuple):
        result = [_jsonable(item) for item in value]
    else:
        raise StateStoreError(f"detail value of type {type(value).__name__} is not JSON-safe")
    return result


def _transition_payload(
    detail: object, legs_json: object
) -> tuple[dict[str, object], str, str | None]:
    if detail is not None and not isinstance(detail, Mapping):
        raise StateStoreError("detail must be a mapping")
    detail_obj = cast(dict[str, object], _jsonable(dict(detail or {})))
    legs_canonical: str | None = None
    if legs_json is not None:
        legs = _load_json(legs_json, "legs_json")
        if not isinstance(legs, list):
            raise StateStoreError("legs_json must be a JSON list")
        legs_canonical = dumps(legs)
    return detail_obj, dumps(detail_obj), legs_canonical


def _record_float(value: object) -> float | None:
    """Risk-decision floats: finite as-is; non-finite (only in recorded rejections) as null."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StateStoreError("risk decision numbers must be numbers or None")
    return float(value) if math.isfinite(value) else None


def _proposal_dict(p: EntryProposal) -> dict[str, object]:
    if not isinstance(p, EntryProposal):
        raise StateStoreError("risk decision proposal must be an EntryProposal")
    for name in ("symbol", "strategy", "strategy_version"):
        if type(getattr(p, name)) is not str:
            raise StateStoreError(f"proposal {name} must be a string")
    for name in ("rank", "proposed_shares"):
        if not is_int(getattr(p, name)):
            raise StateStoreError(f"proposal {name} must be an int")
    if not isinstance(p.side, OrderSide):
        raise StateStoreError("proposal side must be an OrderSide")
    return {
        "symbol": p.symbol,
        "decision_session": date_text(p.decision_session, "proposal decision_session"),
        "data_as_of": date_text(p.data_as_of, "proposal data_as_of"),
        "strategy": p.strategy,
        "strategy_version": p.strategy_version,
        "rank": p.rank,
        "score": _record_float(p.score),
        "entry_reference": _record_float(p.entry_reference),
        "atr": _record_float(p.atr),
        "stop": _record_float(p.stop),
        "target": _record_float(p.target),
        "proposed_shares": p.proposed_shares,
        "side": p.side.value,
    }


def _check_dict(c: RiskCheck) -> dict[str, object]:
    if not isinstance(c, RiskCheck):
        raise StateStoreError("risk decision checks must be RiskCheck records")
    if type(c.check_id) is not str or type(c.passed) is not bool or type(c.detail) is not str:
        raise StateStoreError("risk check id/passed/detail have the wrong types")
    return {
        "check_id": c.check_id,
        "passed": c.passed,
        "observed": _record_float(c.observed),
        "limit": _record_float(c.limit),
        "detail": c.detail,
    }


def _decode_float(value: object, field: str) -> float:
    if value is None:
        return math.nan  # recorded as null because it was non-finite (rejections only)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StateStoreError(f"stored {field} is not a number")
    return float(value)


def _decode_opt_float(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _decode_float(value, field)


def _load_json(value: object, field: str) -> Any:
    if type(value) is not str:
        raise StateStoreError(f"stored {field} is not text")
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        raise StateStoreError(f"stored {field} is not JSON") from None


def _ny_date(now: datetime) -> date:
    return now.astimezone(_NEW_YORK).date()


class StateStore(StateReaderBase):
    """The single WRITER (engine process; CLI when the engine is stopped); a ``StateReader``."""

    def __init__(
        self,
        path: Path,
        *,
        data_source: Literal["real", "synthetic"],
        now: datetime,
    ) -> None:
        """Take the writer lock (§5.3), create or migrate the DB and set the PRAGMAs.

        ``now`` (tz-aware; callers pass ``Clock.now()``) stamps ``meta.created_at`` and
        ``schema_version.applied_at`` when the DB is created or migrated; it is required so that
        ``semiedge.state`` never reads the wall clock.
        An existing DB whose ``meta.data_source`` differs, an unknown newer schema version, or a
        file that is not a SEMI-EDGE state DB is refused with :class:`StateStoreError`.
        """
        if data_source not in DATA_SOURCES:
            raise StateStoreError(f"data_source must be 'real' or 'synthetic', got {data_source!r}")
        if not isinstance(path, Path):
            raise StateStoreError("path must be a pathlib.Path")
        self._depth = 0
        self._pending_halts: list[_PendingHalt] = []
        ts_text(now, "now")  # validate before touching the filesystem
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock: WriterLock | None = WriterLock(path.parent / LOCK_FILE_NAME)
        try:
            conn = sqlite3.connect(path, isolation_level=None)
        except sqlite3.Error as exc:
            self._release_lock()
            raise StateStoreError(f"cannot open state database: {exc}") from None
        try:
            _configure(conn)
            self._store_id, self._data_source = _migrate(conn, data_source, now)
        except BaseException:
            conn.close()
            self._release_lock()
            raise
        self._conn = conn
        self._path = path

    # ------------------------------------------------------------ lifecycle / transactions

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        """Close the connection and release the writer lock (idempotent)."""
        conn = getattr(self, "_conn", None)
        if conn is not None:
            del self._conn
            conn.close()
        self._release_lock()

    def _release_lock(self) -> None:
        lock, self._lock = getattr(self, "_lock", None), None
        if lock is not None:
            lock.release()

    def transaction(self) -> AbstractContextManager[None]:
        """BEGIN IMMEDIATE ... COMMIT/ROLLBACK; re-entrant (nested calls join the outer one)."""
        return self._txn_none()

    @contextmanager
    def _txn_none(self) -> Iterator[None]:
        with self._txn():
            yield

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Connection]:
        conn = self._connection()
        if self._depth > 0:
            savepoint = f"sp{self._depth}"
            conn.execute(f"SAVEPOINT {savepoint}")
            self._depth += 1
            try:
                yield conn
            except BaseException as exc:
                conn.execute(f"ROLLBACK TO {savepoint}")
                conn.execute(f"RELEASE {savepoint}")
                if isinstance(exc, sqlite3.Error):
                    raise StateStoreError(f"state database error: {exc}") from exc
                raise
            else:
                conn.execute(f"RELEASE {savepoint}")
            finally:
                self._depth -= 1
            return
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise StateStoreError(f"cannot begin a write transaction: {exc}") from exc
        self._depth = 1
        try:
            yield conn
            self._write_pending_halts(conn)
            conn.execute("COMMIT")
        except BaseException as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            self._depth = 0
            self._flush_pending_halts_after_rollback(conn)
            if isinstance(exc, sqlite3.Error):
                raise StateStoreError(f"state database error: {exc}") from exc
            raise
        finally:
            self._depth = 0

    def _halting(self, code: str, detail: str, now: str) -> StateStoreError:
        """An error that also records a RECONCILIATION halt when the outer transaction ends."""
        reason = f"{code}: {detail}"[:_MAX_REASON]
        self._pending_halts.append(_PendingHalt(reason, now))
        return StateStoreError(reason)

    def _write_pending_halts(self, conn: sqlite3.Connection) -> None:
        pending, self._pending_halts = self._pending_halts, []
        for halt in pending:
            _insert_halt(conn, HaltKind.RECONCILIATION, halt.reason, _ENGINE, halt.now)

    def _flush_pending_halts_after_rollback(self, conn: sqlite3.Connection) -> None:
        if not self._pending_halts:
            return
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._write_pending_halts(conn)
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise StateStoreError(f"could not record the RECONCILIATION halt: {exc}") from exc
        finally:
            self._pending_halts = []

    # ------------------------------------------------------------ runs / heartbeats

    def start_run(  # noqa: PLR0917 - positional signature fixed by the spec (§5.1)
        self,
        run_id: str,
        kind: str,
        mode: TradingMode,
        fingerprint: str,
        git_sha: str | None,
        now: datetime,
    ) -> None:
        run_id = text(run_id, "run_id", max_len=128)
        if kind not in RUN_KINDS:
            raise StateStoreError(f"run kind must be one of {sorted(RUN_KINDS)}")
        mode = _mode(mode)
        fingerprint = text(fingerprint, "fingerprint", max_len=128)
        if git_sha is not None:
            git_sha = text(git_sha, "git_sha", max_len=64)
        ts = ts_text(now, "now")
        with self._txn() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, kind, started_at, ended_at, status, mode, git_sha, "
                "config_fingerprint, detail) VALUES (?, ?, ?, NULL, 'running', ?, ?, ?, NULL)",
                (run_id, kind, ts, mode.value, git_sha, fingerprint),
            )

    def finish_run(
        self,
        run_id: str,
        status: Literal["ok", "failed", "aborted", "skipped"],
        detail: str,
        now: datetime,
    ) -> None:
        if status not in RUN_FINAL_STATUSES:
            raise StateStoreError(f"run status must be one of {sorted(RUN_FINAL_STATUSES)}")
        if type(detail) is not str or len(detail) > _MAX_REASON:
            raise StateStoreError("run detail must be a string")
        ts = ts_text(now, "now")
        with self._txn() as conn:
            row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise StateStoreError(f"unknown run {run_id!r}")
            if row[0] != "running":
                raise StateStoreError(f"run {run_id!r} is already finished ({row[0]})")
            conn.execute(
                "UPDATE runs SET status = ?, ended_at = ?, detail = ? WHERE run_id = ?",
                (status, ts, detail, run_id),
            )

    def heartbeat(self, component: str, status: str, detail: str, now: datetime) -> None:
        component = text(component, "component", max_len=64)
        status = text(status, "status", max_len=64)
        if type(detail) is not str or len(detail) > _MAX_REASON:
            raise StateStoreError("heartbeat detail must be a string")
        ts = ts_text(now, "now")
        with self._txn() as conn:
            conn.execute(
                "INSERT INTO heartbeats (component, ts, status, detail) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (component) DO UPDATE SET ts = excluded.ts, "
                "status = excluded.status, detail = excluded.detail",
                (component, ts, status, detail),
            )

    # ------------------------------------------------------------ decisions

    def record_signals(
        self,
        run_id: str,
        session: date,
        evaluations: Sequence[SignalEvaluation],
        *,
        sizes: Mapping[str, SizingResult],
    ) -> None:
        session_text = date_text(session, "session")
        if not isinstance(sizes, Mapping):
            raise StateStoreError("sizes must be a mapping of symbol -> SizingResult")
        symbols = set()
        rows: list[tuple[object, ...]] = []
        for ev in evaluations:
            if not isinstance(ev, SignalEvaluation):
                raise StateStoreError("evaluations must be SignalEvaluation records")
            symbol = _symbol(ev.symbol)
            if symbol in symbols:
                raise StateStoreError(f"duplicate evaluation for {symbol}")
            symbols.add(symbol)
            size = sizes.get(symbol)
            if size is not None and not isinstance(size, SizingResult):
                raise StateStoreError(f"{symbol}: size must be a SizingResult")
            if type(ev.eligible) is not bool:
                raise StateStoreError(f"{symbol}: eligible must be a bool")
            if ev.rank is not None and not is_int(ev.rank):
                raise StateStoreError(f"{symbol}: rank must be an int or None")
            score = ev.score if ev.score is not None and is_finite(ev.score) else None
            payload = dumps(evaluation_to_dict(ev, size))
            rows.append((run_id, session_text, symbol, int(ev.eligible), score, ev.rank, payload))
        extra = set(sizes) - symbols
        if extra:
            raise StateStoreError(f"sizes for symbols that were not evaluated: {sorted(extra)}")
        with self._txn() as conn:
            _require_run(conn, run_id)
            conn.executemany(
                "INSERT INTO signals (run_id, session, symbol, eligible, score, rank, "
                "payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def record_risk_decisions(
        self, run_id: str, decisions: Sequence[RiskDecision]
    ) -> tuple[int, ...]:
        rows: list[tuple[object, ...]] = []
        for d in decisions:
            if not isinstance(d, RiskDecision):
                raise StateStoreError("decisions must be RiskDecision records")
            if type(d.approved) is not bool or not is_int(d.approved_shares):
                raise StateStoreError("risk decision approved/approved_shares have wrong types")
            if type(d.limits_fingerprint) is not str or not d.limits_fingerprint:
                raise StateStoreError("risk decision limits_fingerprint must be a string")
            if any(type(r) is not str for r in d.reasons):
                raise StateStoreError("risk decision reasons must be strings")
            proposal = _proposal_dict(d.proposal)
            if d.approved and (
                d.approved_shares < 1
                or d.approved_shares > d.proposal.proposed_shares
                or d.proposal.side is not OrderSide.BUY
                or any(proposal[k] is None for k in ("score", "entry_reference", "atr", "stop"))
                or proposal["target"] is None
            ):
                raise StateStoreError(f"{d.proposal.symbol}: inconsistent approved decision")
            rows.append(
                (
                    run_id,
                    ts_text(d.decided_at, "decided_at"),
                    proposal["decision_session"],
                    _symbol(d.proposal.symbol),
                    int(d.approved),
                    d.proposal.proposed_shares,
                    d.approved_shares,
                    dumps(list(d.reasons)),
                    dumps([_check_dict(c) for c in d.checks]),
                    d.limits_fingerprint,
                    dumps(proposal),
                )
            )
        ids: list[int] = []
        with self._txn() as conn:
            _require_run(conn, run_id)
            for row in rows:
                cursor = conn.execute(
                    "INSERT INTO risk_decisions (run_id, decided_at, session, symbol, approved, "
                    "proposed_shares, approved_shares, reasons_json, checks_json, "
                    "limits_fingerprint, proposal_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
                ids.append(cast(int, cursor.lastrowid))
        return tuple(ids)

    def risk_decision(self, decision_id: int) -> RiskDecision:
        if not is_int(decision_id):
            raise StateStoreError("decision_id must be an int")
        row = (
            self._connection()
            .execute(
                "SELECT approved, approved_shares, reasons_json, checks_json, "
                "limits_fingerprint, proposal_json, decided_at FROM risk_decisions WHERE id = ?",
                (decision_id,),
            )
            .fetchone()
        )
        if row is None:
            raise StateStoreError(f"unknown risk decision {decision_id}")
        approved, approved_shares, reasons_json, checks_json, fingerprint, proposal_json, at = row
        p = _load_json(proposal_json, "proposal_json")
        reasons = _load_json(reasons_json, "reasons_json")
        checks = _load_json(checks_json, "checks_json")
        if not isinstance(p, dict) or not isinstance(reasons, list) or not isinstance(checks, list):
            raise StateStoreError("stored risk decision JSON has the wrong shape")
        try:
            proposal = EntryProposal(
                symbol=str(p["symbol"]),
                decision_session=date.fromisoformat(p["decision_session"]),
                data_as_of=date.fromisoformat(p["data_as_of"]),
                strategy=str(p["strategy"]),
                strategy_version=str(p["strategy_version"]),
                rank=int(p["rank"]),
                score=_decode_float(p["score"], "score"),
                entry_reference=_decode_float(p["entry_reference"], "entry_reference"),
                atr=_decode_float(p["atr"], "atr"),
                stop=_decode_float(p["stop"], "stop"),
                target=_decode_float(p["target"], "target"),
                proposed_shares=int(p["proposed_shares"]),
                side=OrderSide(p["side"]),
            )
            check_records = tuple(
                RiskCheck(
                    check_id=str(c["check_id"]),
                    passed=bool(c["passed"]),
                    observed=_decode_opt_float(c["observed"], "observed"),
                    limit=_decode_opt_float(c["limit"], "limit"),
                    detail=str(c["detail"]),
                )
                for c in checks
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StateStoreError(f"stored risk decision {decision_id} is malformed") from exc
        return RiskDecision(
            proposal=proposal,
            approved=bool(approved),
            approved_shares=int(approved_shares),
            reasons=tuple(str(r) for r in reasons),
            checks=check_records,
            limits_fingerprint=str(fingerprint),
            decided_at=datetime.fromisoformat(at).astimezone(UTC),
        )

    # ------------------------------------------------------------ orders

    def get_order(self, client_order_id: str) -> OrderRecord | None:
        row = (
            self._connection()
            .execute(
                f"SELECT {ORDER_COLUMNS} FROM orders WHERE client_order_id = ?",  # noqa: S608
                (client_order_id,),
            )
            .fetchone()
        )
        return None if row is None else decode_order(row)

    def _order(self, client_order_id: str) -> OrderRecord:
        record = self.get_order(client_order_id)
        if record is None:
            raise StateStoreError(f"unknown order {client_order_id!r}")
        return record

    def insert_order_intent(self, intent: OrderIntent) -> None:
        """Insert an intent with status ``staged`` plus its event row (§5.3, §6.2, §6.3)."""
        values = _validate_intent(intent)
        created = values["created_at"]
        with self._txn() as conn:
            self._check_intent_links(conn, intent)
            conn.execute(
                "INSERT INTO orders (client_order_id, broker_order_id, risk_decision_id, "
                "parent_client_order_id, symbol, side, kind, order_type, time_in_force, "
                "order_class, qty, limit_price, stop_price, decision_session, valid_until, "
                "status, filled_qty, filled_avg_price, legs_json, created_at, submitted_at, "
                "updated_at, last_error) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'staged', 0, NULL, '[]', ?, NULL, ?, NULL)",
                (
                    intent.client_order_id,
                    intent.risk_decision_id,
                    intent.parent_client_order_id,
                    intent.symbol,
                    intent.side.value,
                    intent.kind,
                    intent.order_type,
                    intent.time_in_force,
                    intent.order_class,
                    intent.qty,
                    intent.limit_price,
                    intent.stop_price,
                    values["decision_session"],
                    values["valid_until"],
                    created,
                    created,
                ),
            )
            _insert_event(
                conn,
                intent.client_order_id,
                ts=created,
                event="staged",
                from_status=None,
                to_status="staged",
                broker_status=None,
                filled_qty=0,
                price=None,
                detail_json="{}",
            )

    def _check_intent_links(self, conn: sqlite3.Connection, intent: OrderIntent) -> None:
        live = conn.execute(
            "SELECT client_order_id, status FROM orders WHERE symbol = ? AND kind = ?",
            (intent.symbol, intent.kind),
        ).fetchall()
        open_rows = [cid for cid, status in live if status not in TERMINAL_STATUSES]
        if open_rows:
            raise StateStoreError(
                f"{intent.symbol}: a non-terminal {intent.kind} order already exists "
                f"({open_rows[0]})"
            )
        if intent.kind == "entry":
            row = conn.execute(
                "SELECT approved, approved_shares, symbol, session FROM risk_decisions "
                "WHERE id = ?",
                (intent.risk_decision_id,),
            ).fetchone()
            if row is None:
                raise StateStoreError("entry intent references an unknown risk decision")
            approved, approved_shares, symbol, session = row
            if not approved:
                raise StateStoreError("entry intent references a rejected risk decision")
            if symbol != intent.symbol or session != intent.decision_session.isoformat():
                raise StateStoreError("entry intent symbol/session differ from its decision")
            if intent.qty != approved_shares:
                raise StateStoreError("entry qty must equal the decision's approved_shares")
            earlier = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE kind = 'entry' AND symbol = ? AND "
                "decision_session = ?",
                (intent.symbol, intent.decision_session.isoformat()),
            ).fetchone()[0]
            if earlier:
                raise StateStoreError("an entry is staged once per decision (no attempt > 1)")
        else:
            parent = conn.execute(
                "SELECT kind, symbol FROM orders WHERE client_order_id = ?",
                (intent.parent_client_order_id,),
            ).fetchone()
            if parent is None or parent[0] != "entry" or parent[1] != intent.symbol:
                raise StateStoreError(
                    f"{intent.kind} intent must reference the entry order of the same symbol"
                )

    def next_attempt(self, kind: OrderKind, decision_session: date, symbol: str) -> int:
        """1 + rows with this (kind, decision_session, symbol) (§6.2); refuses while one is live."""
        if kind not in ORDER_KINDS:
            raise StateStoreError(f"unknown order kind {kind!r}")
        session = date_text(decision_session, "decision_session")
        symbol = _symbol(symbol)
        with self._txn() as conn:
            statuses = [
                row[0]
                for row in conn.execute(
                    "SELECT status FROM orders WHERE kind = ? AND decision_session = ? AND "
                    "symbol = ?",
                    (kind, session, symbol),
                )
            ]
        if any(s not in TERMINAL_STATUSES for s in statuses):
            raise StateStoreError(
                f"{symbol} {kind} {session}: a previous attempt is not terminal yet"
            )
        if kind == "entry" and statuses:
            raise StateStoreError("entries are never retried (attempt is always 1)")
        attempt = len(statuses) + 1
        if attempt > 99:
            raise StateStoreError("attempt limit (99) reached")
        return attempt

    def transition_order(
        self,
        client_order_id: str,
        to_status: OrderStatus,
        *,
        now: datetime,
        event: str,
        broker_order_id: str | None = None,
        broker_status: str | None = None,
        filled_qty: int | None = None,
        filled_avg_price: float | None = None,
        legs_json: str | None = None,
        detail: Mapping[str, object] | None = None,
    ) -> None:
        """The only status-changing path (§6.4); writes ``orders`` + ``order_events`` together.

        An unknown or disallowed target status is an illegal transition: ``StateStoreError``
        plus a RECONCILIATION halt (F-122). ``filled_qty`` may increase only to the watermark of
        the ingested fills (F-123); a decrease halts.
        """
        ts = ts_text(now, "now")
        event = text(event, "event", max_len=64)
        if broker_status is not None:
            broker_status = text(broker_status, "broker_status", max_len=64)
        detail_obj, detail_json, legs_canonical = _transition_payload(detail, legs_json)
        with self._txn() as conn:
            if to_status not in ORDER_STATUSES:
                raise self._halting(
                    "illegal_transition",
                    f"{client_order_id}: unknown target status {to_status!r}",
                    ts,
                )
            order = self._order(client_order_id)
            current = order.status
            if to_status not in ALLOWED[current]:
                raise self._halting(
                    "illegal_transition", f"{client_order_id}: {current} -> {to_status}", ts
                )
            if (current, to_status) == RECOVERY_EDGE and broker_order_id is not None:
                raise StateStoreError(
                    f"{client_order_id}: recovery refused, a recovered row has no broker order id"
                )
            new_filled = self._check_fill_fields(order, filled_qty, filled_avg_price, ts)
            new_broker_id = self._resolve_broker_id(conn, order, broker_order_id, ts)
            if to_status == "filled" and new_filled != order.intent.qty:
                raise StateStoreError(
                    f"{client_order_id}: cannot mark filled at {new_filled}/"
                    f"{order.intent.qty}; ingest the broker fills first"
                )
            if to_status == current:
                self._check_self_transition(conn, order, legs_canonical, new_filled)
            submitted_at = (
                ts_text(order.submitted_at, "submitted_at") if order.submitted_at else None
            )
            if (current, to_status) == RECOVERY_EDGE:
                self._check_recovery(conn, order, event, detail_obj, now)
                submitted_at = None
            elif current == "staged" and to_status == "submitting":
                self._check_begin_submit(conn, order, now)
                submitted_at = ts
            last_error = order.last_error
            error = detail_obj.get("error")
            if isinstance(error, str) and error:
                last_error = error[:_MAX_REASON]
            self._apply_transition(
                conn,
                order,
                to_status=to_status,
                ts=ts,
                event=event,
                broker_status=broker_status,
                filled_qty=new_filled,
                filled_avg_price=order.filled_avg_price,
                broker_order_id=new_broker_id,
                legs_json=legs_canonical if legs_canonical is not None else order.legs_json,
                submitted_at=submitted_at,
                last_error=last_error,
                detail_json=detail_json,
            )

    def _apply_transition(
        self,
        conn: sqlite3.Connection,
        order: OrderRecord,
        *,
        to_status: OrderStatus,
        ts: str,
        event: str,
        broker_status: str | None,
        filled_qty: int,
        filled_avg_price: float | None,
        broker_order_id: str | None,
        legs_json: str,
        submitted_at: str | None,
        last_error: str | None,
        detail_json: str,
    ) -> None:
        """The ONE ``UPDATE orders`` path (§5.1, F-123), used by ``transition_order`` and by
        ``ingest_order_fills`` (which records fills with the status unchanged). Writes the
        ``order_events`` row in the same transaction and re-checks the fill invariants."""
        cid = order.intent.client_order_id
        if filled_qty < order.filled_qty:
            raise self._halting(
                "filled_qty_decrease", f"{cid}: {order.filled_qty} -> {filled_qty}", ts
            )
        watermark = _watermark(self._fill_rows(conn, cid))
        if filled_qty != watermark:
            raise StateStoreError(
                f"{cid}: filled_qty {filled_qty} is not consistent with the ingested fills "
                f"(watermark {watermark}); ingest fills first (ingest_order_fills)"
            )
        if filled_avg_price is not None and not (
            math.isfinite(filled_avg_price) and filled_avg_price > 0
        ):
            raise StateStoreError(f"{cid}: derived filled_avg_price is not finite and > 0")
        if (filled_qty > 0) != (filled_avg_price is not None):
            raise StateStoreError(f"{cid}: filled_avg_price must be set exactly when filled")
        conn.execute(
            "UPDATE orders SET status = ?, broker_order_id = ?, filled_qty = ?, "
            "filled_avg_price = ?, legs_json = ?, submitted_at = ?, updated_at = ?, "
            "last_error = ? WHERE client_order_id = ?",
            (
                to_status,
                broker_order_id,
                filled_qty,
                filled_avg_price,
                legs_json,
                submitted_at,
                ts,
                last_error,
                cid,
            ),
        )
        _insert_event(
            conn,
            cid,
            ts=ts,
            event=event,
            from_status=order.status,
            to_status=to_status,
            broker_status=broker_status,
            filled_qty=filled_qty,
            price=filled_avg_price,
            detail_json=detail_json,
        )

    def _check_fill_fields(
        self, order: OrderRecord, filled_qty: object, filled_avg_price: object, ts: str
    ) -> int:
        """The filled qty to record: an explicit value may only increase (a decrease halts)
        and must match the ingested fills (checked in ``_apply_transition``)."""
        cid = order.intent.client_order_id
        new_filled = order.filled_qty
        if filled_qty is not None:
            if not is_int(filled_qty) or cast(int, filled_qty) < 0:
                raise StateStoreError("filled_qty must be an int >= 0")
            new_filled = cast(int, filled_qty)
            if new_filled < order.filled_qty:
                raise self._halting(
                    "filled_qty_decrease", f"{cid}: {order.filled_qty} -> {new_filled}", ts
                )
        if filled_avg_price is not None:
            price = _positive_price(filled_avg_price, "filled_avg_price")
            stored = order.filled_avg_price
            if stored is None or not math.isclose(price, stored, rel_tol=1e-9):
                raise StateStoreError(f"{cid}: filled_avg_price differs from the ingested fills")
        return new_filled

    def _resolve_broker_id(
        self, conn: sqlite3.Connection, order: OrderRecord, broker_order_id: object, ts: str
    ) -> str | None:
        """A broker order id is set once and belongs to exactly one local order."""
        if broker_order_id is None:
            return order.broker_order_id
        cid = order.intent.client_order_id
        new_id = text(broker_order_id, "broker_order_id", max_len=128)
        if order.broker_order_id not in (None, new_id):
            raise self._halting(
                "broker_order_id_mismatch", f"{cid}: {order.broker_order_id} -> {new_id}", ts
            )
        other = conn.execute(
            "SELECT client_order_id FROM orders WHERE broker_order_id = ? AND client_order_id <> ?",
            (new_id, cid),
        ).fetchone()
        if other is not None:
            raise self._halting(
                "broker_order_id_mismatch", f"{new_id} already belongs to {other[0]}", ts
            )
        return new_id

    def _check_self_transition(
        self, conn: sqlite3.Connection, order: OrderRecord, legs: str | None, filled: int
    ) -> None:
        """Self-transitions only record new fills or new legs (§5.1)."""
        if legs is not None and legs != order.legs_json:
            return
        row = conn.execute(
            "SELECT filled_qty FROM order_events WHERE client_order_id = ? AND "
            "to_status IS NOT NULL AND event <> 'fills_ingested' ORDER BY id DESC LIMIT 1",
            (order.intent.client_order_id,),
        ).fetchone()
        recorded = row[0] if row is not None and row[0] is not None else 0
        if filled > recorded:
            return
        raise StateStoreError(
            f"{order.intent.client_order_id}: {order.status} -> {order.status} records no new "
            "fills or legs"
        )

    def _check_begin_submit(
        self, conn: sqlite3.Connection, order: OrderRecord, now: datetime
    ) -> None:
        intent = order.intent
        if now >= intent.valid_until:
            raise StateStoreError(f"{intent.client_order_id}: intent is past valid_until")
        if intent.kind == "entry":
            halts = conn.execute("SELECT kind FROM halts WHERE cleared_at IS NULL").fetchall()
            if halts:
                kinds = sorted({h[0] for h in halts})
                raise StateStoreError(f"{intent.client_order_id}: entries are halted ({kinds})")
        if intent.side is OrderSide.SELL:
            placeholders = ", ".join("?" * len(SENT_NON_TERMINAL))
            other = conn.execute(
                "SELECT client_order_id FROM orders WHERE symbol = ? AND side = 'sell' AND "  # noqa: S608
                f"client_order_id <> ? AND status IN ({placeholders})",
                (intent.symbol, intent.client_order_id, *sorted(SENT_NON_TERMINAL)),
            ).fetchone()
            if other is not None:
                raise StateStoreError(
                    f"{intent.symbol}: another sell ({other[0]}) is live; one sent sell at a time"
                )

    def _check_recovery(
        self,
        conn: sqlite3.Connection,
        order: OrderRecord,
        event: str,
        detail: Mapping[str, object],
        now: datetime,
    ) -> None:
        """The §6.4 recovery exception ``unknown -> staged``; anything short of it is refused."""
        cid = order.intent.client_order_id
        if event != RECOVERED_UNSENT_EVENT:
            raise StateStoreError(f"{cid}: unknown -> staged needs event {RECOVERED_UNSENT_EVENT}")
        if now >= order.intent.valid_until:
            raise StateStoreError(f"{cid}: recovery refused, intent is past valid_until")
        if order.broker_order_id is not None or order.filled_qty:
            raise StateStoreError(f"{cid}: recovery refused, the broker knows this order")
        fills = conn.execute(
            "SELECT COUNT(*) FROM fills WHERE client_order_id = ?", (cid,)
        ).fetchone()[0]
        if fills:
            raise StateStoreError(f"{cid}: recovery refused, fill rows exist")
        if order.submitted_at is None:
            raise StateStoreError(f"{cid}: recovery refused, no recorded submission attempt")
        lookups = detail.get("lookup_404_at")
        if not isinstance(lookups, list) or len(lookups) < 2:
            raise StateStoreError(f"{cid}: recovery refused, needs two confirmed 404 lookups")
        cycle = detail.get("monitor_cycle_seconds")
        # F-140: a sub-second cycle (e.g. 1e-10) would round the spacing to zero; real monitor
        # cycles are at least a second, so anything smaller is not credible evidence.
        if not is_finite(cycle) or cast(float, cycle) < 1:
            raise StateStoreError(
                f"{cid}: recovery refused, needs monitor_cycle_seconds (finite >= 1) as "
                "spacing evidence"
            )
        spacing = timedelta(seconds=float(cast(float, cycle)))
        parsed = _recovery_lookups(cid, lookups, order.submitted_at, now)
        if any(b <= a or b - a < spacing for a, b in itertools.pairwise(parsed)):
            raise StateStoreError(
                f"{cid}: recovery refused, lookups must be at least one monitor cycle apart"
            )
        if detail.get("absent_from_list_orders_all") is not True:
            raise StateStoreError(
                f"{cid}: recovery refused, needs absence from list_orders(status='all')"
            )

    # ------------------------------------------------------------ fills (§6.4, F-038/F-044/F-046)

    def ingest_order_fills(
        self,
        client_order_id: str,
        *,
        broker_filled_qty: int,
        broker_avg_price: float | None,
        activities: Sequence[BrokerFillInput] | None,
        now: datetime,
    ) -> FillIngestResult:
        """§6.4 fill ingestion by the per-order watermark ``W = max(A, P)``; one transaction.

        ``activities=None`` means the activities endpoint was unavailable this cycle. Raises
        :class:`StateStoreError` (and records a RECONCILIATION halt, FILL_MISMATCH) if the local
        cumulative would exceed ``broker_filled_qty``; nothing of this call is written then.
        """
        ts = ts_text(now, "now")
        if not is_int(broker_filled_qty) or broker_filled_qty < 0:
            raise StateStoreError("broker_filled_qty must be an int >= 0")
        avg = _opt_price(broker_avg_price, "broker_avg_price")
        acts = _validate_activities(activities)
        with self._txn() as conn:
            order = self._order(client_order_id)
            cid = order.intent.client_order_id
            b = broker_filled_qty
            if order.status in UNSENT_STATUSES:
                if b > 0 or acts:
                    raise self._halting(
                        "fill_mismatch", f"{cid}: fills reported for an unsent order", ts
                    )
                return FillIngestResult(0, (), (), order.filled_qty)
            rows = self._checked_fill_rows(conn, order, b, ts)
            w_old = order.filled_qty
            inserted: list[str] = []
            superseded: list[str] = []
            if acts is not None:
                inserted, superseded = self._ingest_activities(conn, cid, rows, acts, ts)
                rows = self._fill_rows(conn, cid)
            w = _watermark(rows)
            if w > b:
                raise self._halting(
                    "fill_mismatch", f"{cid}: activities sum to {w} > broker cumulative {b}", ts
                )
            if b > w:
                if avg is None:
                    raise StateStoreError(
                        f"{cid}: broker reports {b} filled without an average price"
                    )
                key = f"{cid}:{b}"
                if conn.execute("SELECT 1 FROM fills WHERE fill_key = ?", (key,)).fetchone():
                    raise self._halting("fill_mismatch", f"{cid}: fill key {key} reused", ts)
                conn.execute(
                    "INSERT INTO fills (fill_key, client_order_id, ts, qty, price, source, "
                    "status, cum_qty, superseded_at) "
                    "VALUES (?, ?, ?, ?, ?, 'cumulative', 'provisional', ?, NULL)",
                    (key, cid, ts, b - w, avg, b),
                )
                inserted.append(key)
                rows = self._fill_rows(conn, cid)
                w = b
            avg_price = _order_avg_price(rows, avg, order.filled_avg_price)
            changed = (w, avg_price) != (order.filled_qty, order.filled_avg_price)
            if inserted or superseded or changed:
                self._apply_transition(
                    conn,
                    order,
                    to_status=order.status,  # fills are recorded; the status is unchanged
                    ts=ts,
                    event="fills_ingested",
                    broker_status=None,
                    filled_qty=w,
                    filled_avg_price=avg_price,
                    broker_order_id=order.broker_order_id,
                    legs_json=order.legs_json,
                    submitted_at=(
                        None
                        if order.submitted_at is None
                        else ts_text(order.submitted_at, "submitted_at")
                    ),
                    last_error=order.last_error,
                    detail_json=dumps(
                        {"broker_filled_qty": b, "inserted": inserted, "superseded": superseded}
                    ),
                )
            return FillIngestResult(
                added_qty=w - w_old,
                inserted=tuple(inserted),
                superseded=tuple(superseded),
                local_filled_qty=w,
            )

    def _checked_fill_rows(
        self, conn: sqlite3.Connection, order: OrderRecord, b: int, ts: str
    ) -> list[FillRecord]:
        """The order's fill rows, after checking the stored and broker cumulatives agree."""
        cid = order.intent.client_order_id
        if b > order.intent.qty:
            raise self._halting(
                "fill_mismatch", f"{cid}: broker filled {b} > order qty {order.intent.qty}", ts
            )
        rows = self._fill_rows(conn, cid)
        w_old = _watermark(rows)
        if w_old != order.filled_qty:
            raise self._halting(
                "fill_mismatch",
                f"{cid}: orders.filled_qty {order.filled_qty} != fill watermark {w_old}",
                ts,
            )
        if b < w_old:
            raise self._halting(
                "fill_mismatch", f"{cid}: broker cumulative {b} below local {w_old}", ts
            )
        return rows

    def _ingest_activities(
        self,
        conn: sqlite3.Connection,
        cid: str,
        rows: Sequence[FillRecord],
        acts: Sequence[BrokerFillInput],
        ts: str,
    ) -> tuple[list[str], list[str]]:
        """§6.4 step 1: insert unseen activities as ``final``; supersede covered provisionals."""
        inserted: list[str] = []
        superseded: list[str] = []
        a_sum = sum(r.qty for r in rows if r.status == "final")
        p_max = max((r.cum_qty for r in rows if r.status == "provisional"), default=0)
        for act in acts:
            if self._known_activity(conn, cid, act, ts):
                continue
            a_sum += act.qty
            conn.execute(
                "INSERT INTO fills (fill_key, client_order_id, ts, qty, price, source, status, "
                "cum_qty, superseded_at) VALUES (?, ?, ?, ?, ?, 'activity', 'final', ?, NULL)",
                (
                    act.activity_id,
                    cid,
                    ts_text(act.ts, "activity ts"),
                    act.qty,
                    act.price,
                    max(a_sum, p_max),
                ),
            )
            inserted.append(act.activity_id)
        for row in rows:
            if row.status == "provisional" and row.cum_qty <= a_sum:
                conn.execute(
                    "UPDATE fills SET status = 'superseded', superseded_at = ? WHERE fill_key = ?",
                    (ts, row.fill_key),
                )
                superseded.append(row.fill_key)
        return inserted, superseded

    def _fill_rows(self, conn: sqlite3.Connection, cid: str) -> list[FillRecord]:
        return [
            decode_fill(row)
            for row in conn.execute(
                f"SELECT {FILL_COLUMNS} FROM fills WHERE client_order_id = ? "  # noqa: S608
                "ORDER BY ts, fill_key",
                (cid,),
            )
        ]

    def _known_activity(
        self, conn: sqlite3.Connection, cid: str, act: BrokerFillInput, ts: str
    ) -> bool:
        row = conn.execute(
            "SELECT client_order_id, source, qty, price, ts FROM fills WHERE fill_key = ?",
            (act.activity_id,),
        ).fetchone()
        if row is None:
            return False
        if tuple(row) != (cid, "activity", act.qty, act.price, ts_text(act.ts, "activity ts")):
            raise self._halting(
                "fill_mismatch",
                f"{cid}: activity {act.activity_id} differs from the stored row",
                ts,
            )
        return True

    # ------------------------------------------------------------ corporate actions (§9.4)

    def apply_corporate_action_adjustment(
        self,
        symbol: str,
        *,
        event_id: str,
        ex_date: date,
        new_shares: int,
        old_shares: int,
        now: datetime,
    ) -> bool:
        """Apply a verified split once (F-039/F-045); ``False`` if already recorded."""
        symbol = _symbol(symbol)
        event_id = text(event_id, "event_id", max_len=256)
        ex_text = date_text(ex_date, "ex_date")
        new = _positive_int(new_shares, "new_shares")
        old = _positive_int(old_shares, "old_shares")
        if new == old:
            raise StateStoreError("new_shares and old_shares must differ")
        ts = ts_text(now, "now")
        ratio = Fraction(new, old)
        with self._txn() as conn:
            done = conn.execute(
                "SELECT ex_date, new_shares, old_shares FROM corporate_action_adjustments "
                "WHERE symbol = ? AND event_id = ?",
                (symbol, event_id),
            ).fetchone()
            if done is not None:
                if tuple(done) != (ex_text, new, old):
                    raise StateStoreError(
                        f"{symbol} {event_id}: already applied with different terms {tuple(done)}"
                    )
                return False
            row = conn.execute(
                f"SELECT {POSITION_COLUMNS} FROM positions WHERE symbol = ?",  # noqa: S608
                (symbol,),
            ).fetchone()
            if row is None:
                raise StateStoreError(f"{symbol}: no position to adjust")
            position = decode_position(row)
            if position.status == "closed" or position.qty <= 0:
                raise StateStoreError(f"{symbol}: position is not held")
            if not position.entry_session < ex_date <= _ny_date(now):
                raise StateStoreError(
                    f"{symbol}: ex_date {ex_text} must be after the entry session "
                    f"{position.entry_session} and not in the future"
                )
            scaled = position.qty * ratio
            if scaled.denominator != 1:
                raise StateStoreError(
                    f"{symbol}: {position.qty} shares x {new}:{old} is not a whole number"
                )
            qty_after = int(scaled)
            conn.execute(
                "INSERT INTO corporate_action_adjustments (symbol, ex_date, event_id, new_shares, "
                "old_shares, qty_before, qty_after, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, ex_text, event_id, new, old, position.qty, qty_after, ts),
            )
            conn.execute(
                "UPDATE positions SET qty = ?, avg_entry_price = ?, stop = ?, target = ?, "
                "split_ratio = ?, updated_at = ? WHERE symbol = ?",
                (
                    qty_after,
                    _derived_float(Fraction(position.avg_entry_price) / ratio, "avg_entry_price"),
                    _derived_float(Fraction(position.stop) / ratio, "stop"),
                    _derived_float(Fraction(position.target) / ratio, "target"),
                    _derived_float(Fraction(position.split_ratio) * ratio, "split_ratio"),
                    ts,
                    symbol,
                ),
            )
        return True

    # ------------------------------------------------------------ positions

    def open_position(self, record: PositionRecord) -> None:
        if not isinstance(record, PositionRecord):
            raise StateStoreError("record must be a PositionRecord")
        symbol = _symbol(record.symbol)
        qty = _positive_int(record.qty, "qty")
        prices = {
            name: _positive_price(getattr(record, name), name)
            for name in ("avg_entry_price", "entry_reference", "atr", "stop", "target")
        }
        if record.status != "open" or record.closed_at is not None:
            raise StateStoreError("a new position must be open, with closed_at None")
        if record.split_ratio != 1.0 or type(record.split_ratio) is not float:
            raise StateStoreError("split_ratio starts at 1.0 (changed only by adjustments)")
        session = date_text(record.entry_session, "entry_session")
        opened = ts_text(record.opened_at, "opened_at")
        updated = ts_text(record.updated_at, "updated_at")
        with self._txn() as conn:
            entry = conn.execute(
                "SELECT kind, symbol FROM orders WHERE client_order_id = ?",
                (record.entry_client_order_id,),
            ).fetchone()
            if entry is None or tuple(entry) != ("entry", symbol):
                raise StateStoreError(f"{symbol}: position must reference its entry order")
            existing = conn.execute(
                "SELECT status FROM positions WHERE symbol = ?", (symbol,)
            ).fetchone()
            if existing is not None and existing[0] != "closed":
                raise StateStoreError(f"{symbol}: a position is already open")
            conn.execute("DELETE FROM positions WHERE symbol = ? AND status = 'closed'", (symbol,))
            conn.execute(
                "INSERT INTO positions (symbol, qty, avg_entry_price, entry_session, "
                "entry_client_order_id, entry_reference, atr, stop, target, split_ratio, status, "
                "opened_at, closed_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 'open', ?, NULL, ?)",
                (
                    symbol,
                    qty,
                    prices["avg_entry_price"],
                    session,
                    record.entry_client_order_id,
                    prices["entry_reference"],
                    prices["atr"],
                    prices["stop"],
                    prices["target"],
                    opened,
                    updated,
                ),
            )

    def update_position(
        self,
        symbol: str,
        *,
        now: datetime,
        qty: int | None = None,
        avg_entry_price: float | None = None,
        stop: float | None = None,
        target: float | None = None,
        status: Literal["open", "exiting", "closed"] | None = None,
    ) -> None:
        """Update one position; ``split_ratio`` is changed ONLY by adjustments (F-039)."""
        symbol = _symbol(symbol)
        ts = ts_text(now, "now")
        if (
            qty is None
            and avg_entry_price is None
            and stop is None
            and target is None
            and (status is None)
        ):
            raise StateStoreError("update_position needs at least one field")
        if qty is not None and (not is_int(qty) or qty < 0):
            raise StateStoreError("qty must be an int >= 0")
        if status is not None and status not in ("open", "exiting", "closed"):
            raise StateStoreError(f"unknown position status {status!r}")
        with self._txn() as conn:
            row = conn.execute(
                f"SELECT {POSITION_COLUMNS} FROM positions WHERE symbol = ?",  # noqa: S608
                (symbol,),
            ).fetchone()
            if row is None:
                raise StateStoreError(f"{symbol}: no position")
            p = decode_position(row)
            if p.status == "closed":
                raise StateStoreError(f"{symbol}: position is closed")
            new_status = status if status is not None else p.status
            new_qty = qty if qty is not None else p.qty
            if (new_status == "closed") != (new_qty == 0):
                raise StateStoreError(f"{symbol}: a position is closed exactly when qty is 0")
            conn.execute(
                "UPDATE positions SET qty = ?, avg_entry_price = ?, stop = ?, target = ?, "
                "status = ?, closed_at = ?, updated_at = ? WHERE symbol = ?",
                (
                    new_qty,
                    p.avg_entry_price
                    if avg_entry_price is None
                    else _positive_price(avg_entry_price, "avg_entry_price"),
                    p.stop if stop is None else _positive_price(stop, "stop"),
                    p.target if target is None else _positive_price(target, "target"),
                    new_status,
                    ts if new_status == "closed" else None,
                    ts,
                    symbol,
                ),
            )

    # ------------------------------------------------------------ halts (§8)

    def set_halt(self, kind: HaltKind, reason: str, actor: str, now: datetime) -> int:
        """Idempotent per kind: an already-active halt of the same kind returns its id."""
        if not isinstance(kind, HaltKind):
            raise StateStoreError("kind must be a HaltKind")
        reason = text(reason, "reason", max_len=_MAX_REASON)
        actor = _actor(actor)
        ts = ts_text(now, "now")
        with self._txn() as conn:
            return _insert_halt(conn, kind, reason, actor, ts)

    def clear_halt(self, halt_id: int, actor: str, now: datetime) -> None:
        """``cli:<name>`` clears any kind; ``engine`` only DAILY_LOSS and DATA (§8)."""
        if not is_int(halt_id):
            raise StateStoreError("halt_id must be an int")
        actor = _actor(actor)
        ts = ts_text(now, "now")
        with self._txn() as conn:
            row = conn.execute(
                "SELECT kind, cleared_at FROM halts WHERE id = ?", (halt_id,)
            ).fetchone()
            if row is None or row[1] is not None:
                raise StateStoreError(f"halt {halt_id} is not an active halt")
            if actor == _ENGINE and HaltKind(row[0]) not in _ENGINE_CLEARABLE:
                raise StateStoreError(f"actor 'engine' may not clear a {row[0]} halt")
            conn.execute(
                "UPDATE halts SET cleared_at = ?, cleared_by = ? WHERE id = ? AND "
                "cleared_at IS NULL",
                (ts, actor, halt_id),
            )

    # ------------------------------------------------------------ reconciliation / equity

    def record_reconciliation(self, report: ReconciliationReport) -> int:
        if not isinstance(report, ReconciliationReport):
            raise StateStoreError("report must be a ReconciliationReport")
        started = ts_text(report.started_at, "started_at")
        finished = ts_text(report.finished_at, "finished_at")
        if report.finished_at < report.started_at:
            raise StateStoreError("reconciliation finished before it started")
        if report.outcome not in ("clean", "resolved", "halted", "error"):
            raise StateStoreError(f"unknown reconciliation outcome {report.outcome!r}")
        discrepancies = encode_discrepancies(report.discrepancies)
        with self._txn() as conn:
            cursor = conn.execute(
                "INSERT INTO reconciliation_runs (started_at, finished_at, outcome, "
                "discrepancies_json) VALUES (?, ?, ?, ?)",
                (started, finished, report.outcome, discrepancies),
            )
            return cast(int, cursor.lastrowid)

    def last_reconciliation(self) -> ReconciliationReport | None:
        row = (
            self._connection()
            .execute(
                "SELECT started_at, finished_at, outcome, discrepancies_json "
                "FROM reconciliation_runs ORDER BY id DESC LIMIT 1"
            )
            .fetchone()
        )
        return None if row is None else decode_reconciliation(row)

    def record_equity_snapshot(self, snapshot: EquitySnapshot) -> None:
        if not isinstance(snapshot, EquitySnapshot):
            raise StateStoreError("snapshot must be an EquitySnapshot")
        if snapshot.source not in ("broker_close", "broker_intraday"):
            raise StateStoreError(f"unknown equity snapshot source {snapshot.source!r}")
        values = (
            ts_text(snapshot.ts, "ts"),
            date_text(snapshot.session, "session"),
            _finite(snapshot.equity, "equity"),
            _finite(snapshot.last_equity, "last_equity"),
            _finite(snapshot.cash, "cash"),
            _finite(snapshot.long_market_value, "long_market_value"),
            snapshot.source,
        )
        with self._txn() as conn:
            conn.execute(
                "INSERT INTO equity_snapshots (ts, session, equity, last_equity, cash, "
                "long_market_value, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
                values,
            )

    def peak_close_equity(self) -> float | None:
        row = (
            self._connection()
            .execute("SELECT MAX(equity) FROM equity_snapshots WHERE source = 'broker_close'")
            .fetchone()
        )
        return None if row is None or row[0] is None else float(row[0])

    # ------------------------------------------------------------ outbox

    def enqueue_notification(self, n: Notification) -> int:
        if not isinstance(n, Notification) or not isinstance(n.category, NotificationCategory):
            raise StateStoreError("n must be a Notification")
        mode = _mode(n.mode)
        created = ts_text(n.created_at, "created_at")
        with self._txn() as conn:
            cursor = conn.execute(
                "INSERT INTO notifications_outbox (created_at, category, mode_tag, title, body, "
                "attempts, next_attempt_at, sent_at, last_error) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, NULL, NULL)",
                (created, n.category.value, mode.value, n.title, n.body, created),
            )
            return cast(int, cursor.lastrowid)

    def due_notifications(
        self, now: datetime, limit: int
    ) -> tuple[tuple[int, Notification, int], ...]:
        """(outbox_id, notification, attempts) of unsent rows due at ``now``, oldest first."""
        ts = ts_text(now, "now")
        if not is_int(limit) or limit <= 0:
            raise StateStoreError("limit must be an int > 0")
        rows = (
            self._connection()
            .execute(
                "SELECT id, created_at, category, mode_tag, title, body, attempts "
                "FROM notifications_outbox WHERE sent_at IS NULL AND next_attempt_at <= ? "
                "ORDER BY next_attempt_at, id LIMIT ?",
                (ts, limit),
            )
            .fetchall()
        )
        out: list[tuple[int, Notification, int]] = []
        for outbox_id, created, category, mode_tag, title, body, attempts in rows:
            try:
                n = Notification(
                    category=NotificationCategory(category),
                    mode=TradingMode(mode_tag),
                    title=title,
                    body=body,
                    created_at=datetime.fromisoformat(created).astimezone(UTC),
                )
            except (TypeError, ValueError) as exc:
                raise StateStoreError(f"outbox row {outbox_id} is malformed") from exc
            out.append((outbox_id, n, attempts))
        return tuple(out)

    def mark_notification(
        self,
        outbox_id: int,
        *,
        sent_at: datetime | None,
        error: str | None,
        next_attempt_at: datetime | None,
    ) -> None:
        """Record one delivery attempt: sent (``sent_at``) or failed (``error``)."""
        if not is_int(outbox_id):
            raise StateStoreError("outbox_id must be an int")
        if (sent_at is None) == (error is None):
            raise StateStoreError("exactly one of sent_at and error must be given")
        sent = None if sent_at is None else ts_text(sent_at, "sent_at")
        if error is not None:
            error = text(error, "error", max_len=_MAX_REASON)
        if sent is not None and next_attempt_at is not None:
            raise StateStoreError("a sent notification has no next attempt")
        nxt = None if next_attempt_at is None else ts_text(next_attempt_at, "next_attempt_at")
        with self._txn() as conn:
            row = conn.execute(
                "SELECT sent_at FROM notifications_outbox WHERE id = ?", (outbox_id,)
            ).fetchone()
            if row is None or row[0] is not None:
                raise StateStoreError(f"outbox row {outbox_id} is unknown or already sent")
            conn.execute(
                "UPDATE notifications_outbox SET attempts = attempts + 1, sent_at = ?, "
                "last_error = ?, next_attempt_at = COALESCE(?, next_attempt_at) WHERE id = ?",
                (sent, error, nxt, outbox_id),
            )


# ------------------------------------------------------------------ module helpers


def _mode(mode: object) -> TradingMode:
    if not isinstance(mode, TradingMode) or mode is TradingMode.LIVE:
        raise StateStoreError("mode must be scanner, backtest or paper (never live)")
    return mode


def _configure(conn: sqlite3.Connection) -> None:
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    if mode is None or str(mode[0]).lower() != "wal":
        raise StateStoreError("state database must use journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise StateStoreError("SQLite foreign key enforcement is unavailable")


def _migrate(
    conn: sqlite3.Connection, data_source: Literal["real", "synthetic"], now: datetime
) -> tuple[str, Literal["real", "synthetic"]]:
    """Create or migrate inside one transaction; forward only; refuse unknown newer versions."""
    ts = ts_text(now, "now")
    try:
        conn.execute("BEGIN IMMEDIATE")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not tables:
            applied = 0
            store_id = uuid.uuid4().hex
        elif "schema_version" not in tables or "meta" not in tables:
            raise StateStoreError("existing file is not a SEMI-EDGE state database")
        else:
            store_id, existing_source = read_identity(conn)
            if existing_source != data_source:
                raise StateStoreError(
                    f"state database holds {existing_source!r} data, not {data_source!r}"
                )
            applied = max(row[0] for row in conn.execute("SELECT version FROM schema_version"))
        for version in sorted(MIGRATIONS):
            if version <= applied:
                continue
            for statement in split_statements(MIGRATIONS[version]):
                conn.execute(statement)
            if version == 1:
                conn.executemany(
                    "INSERT INTO meta (key, value) VALUES (?, ?)",
                    (("store_id", store_id), ("created_at", ts), ("data_source", data_source)),
                )
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)", (version, ts)
            )
        result = read_identity(conn)
        conn.execute("COMMIT")
    except BaseException as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        if isinstance(exc, sqlite3.Error):
            raise StateStoreError(f"cannot initialise state database: {exc}") from exc
        raise
    if LATEST_SCHEMA_VERSION < 1:  # pragma: no cover - static guard
        raise StateStoreError("no migrations defined")
    return result


def _require_run(conn: sqlite3.Connection, run_id: object) -> None:
    if conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
        raise StateStoreError(f"unknown run {run_id!r}")


def _insert_halt(conn: sqlite3.Connection, kind: HaltKind, reason: str, actor: str, ts: str) -> int:
    row = conn.execute(
        "SELECT id FROM halts WHERE kind = ? AND cleared_at IS NULL ORDER BY id LIMIT 1",
        (kind.value,),
    ).fetchone()
    if row is not None:
        return cast(int, row[0])
    cursor = conn.execute(
        "INSERT INTO halts (kind, reason, created_at, created_by, cleared_at, cleared_by) "
        "VALUES (?, ?, ?, ?, NULL, NULL)",
        (kind.value, reason, ts, actor),
    )
    return cast(int, cursor.lastrowid)


def _insert_event(
    conn: sqlite3.Connection,
    cid: str,
    *,
    ts: str,
    event: str,
    from_status: str | None,
    to_status: str | None,
    broker_status: str | None,
    filled_qty: int | None,
    price: float | None,
    detail_json: str,
) -> None:
    conn.execute(
        "INSERT INTO order_events (client_order_id, ts, event, from_status, to_status, "
        "broker_status, filled_qty, price, detail_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, ts, event, from_status, to_status, broker_status, filled_qty, price, detail_json),
    )


def _watermark(rows: Sequence[FillRecord]) -> int:
    """``W = max(A, P)`` (§6.4); superseded rows never count."""
    a_sum = sum(r.qty for r in rows if r.status == "final")
    p_max = max((r.cum_qty for r in rows if r.status == "provisional"), default=0)
    return max(a_sum, p_max)


def _order_avg_price(
    rows: Sequence[FillRecord], broker_avg: float | None, stored: float | None
) -> float | None:
    """Order-level average implied by the §6.4 order notional.

    While a provisional row is active the notional is ``W * filled_avg_price`` (the broker's
    order-level average at the latest ingestion), so the average is that broker average;
    otherwise it is ``sum(qty * price) / A`` over the final (activity) rows.
    """
    if any(r.status == "provisional" for r in rows):
        return broker_avg if broker_avg is not None else stored
    finals = [r for r in rows if r.status == "final"]
    if not finals:
        return None
    a_sum = sum(r.qty for r in finals)
    exact = sum((r.qty * Fraction(r.price) for r in finals), Fraction(0)) / a_sum
    return _derived_float(exact, "filled_avg_price")  # exact: never an intermediate overflow


def _recovery_lookups(
    cid: str, lookups: list[object], submitted_at: datetime, now: datetime
) -> list[datetime]:
    """Parse the 404 lookup timestamps: aware, after the last submission attempt, <= now."""
    parsed: list[datetime] = []
    for value in lookups:
        try:
            stamp = datetime.fromisoformat(cast(str, value))
        except (TypeError, ValueError):
            raise StateStoreError(
                f"{cid}: recovery refused, lookup timestamps must be ISO datetimes"
            ) from None
        if stamp.tzinfo is None or stamp > now:
            raise StateStoreError(
                f"{cid}: recovery refused, lookup timestamps must be aware and <= now"
            )
        if stamp <= submitted_at:
            raise StateStoreError(
                f"{cid}: recovery refused, lookups must follow the last submission attempt"
            )
        parsed.append(stamp)
    return parsed


def _derived_float(value: Fraction, field: str) -> float:
    """A derived persisted number: finite and > 0, else StateStoreError (rolls back, F-121)."""
    try:
        result = float(value)
    except OverflowError:
        raise StateStoreError(f"derived {field} is not finite") from None
    if not math.isfinite(result) or result <= 0:
        raise StateStoreError(f"derived {field} is not finite and > 0")
    return result


def _validate_activities(
    activities: object,
) -> list[BrokerFillInput] | None:
    """Validate, de-duplicate identical repeats and sort by (ts, activity_id)."""
    if activities is None:
        return None
    if isinstance(activities, str | bytes) or not isinstance(activities, Sequence):
        raise StateStoreError("activities must be a sequence of BrokerFillInput or None")
    seen: dict[str, BrokerFillInput] = {}
    for act in activities:
        if not isinstance(act, BrokerFillInput):
            raise StateStoreError("activities must be BrokerFillInput records")
        text(act.activity_id, "activity_id", max_len=128)
        _positive_int(act.qty, "activity qty")
        _positive_price(act.price, "activity price")
        ts_text(act.ts, "activity ts")
        previous = seen.get(act.activity_id)
        if previous is not None and previous != act:
            raise StateStoreError(f"activity {act.activity_id} reported twice with different data")
        seen[act.activity_id] = act
    return sorted(seen.values(), key=lambda a: (a.ts, a.activity_id))


def _check_entry_shape(intent: OrderIntent, limit: float | None, stop: float | None) -> None:
    if intent.side is not OrderSide.BUY or not is_int(intent.risk_decision_id):
        raise StateStoreError("an entry is a BUY with a risk_decision_id")
    if intent.parent_client_order_id is not None:
        raise StateStoreError("an entry has no parent order")
    shape = (intent.order_type, intent.time_in_force, intent.order_class)
    if shape != ("market", "opg", "simple") or limit is not None or stop is not None:
        raise StateStoreError("an entry is a simple market order for the opening auction")


def _check_sell_shape(intent: OrderIntent, limit: float | None, stop: float | None) -> None:
    if intent.side is not OrderSide.SELL:
        raise StateStoreError(f"a {intent.kind} order is a SELL")
    if intent.risk_decision_id is not None:
        raise StateStoreError(f"a {intent.kind} order carries no risk decision")
    _client_id(intent.parent_client_order_id)
    shape = (intent.order_type, intent.time_in_force, intent.order_class)
    if intent.kind == "protection":
        if shape != ("limit", "gtc", "oco") or limit is None or stop is None:
            raise StateStoreError("protection is one gtc OCO with limit=target and stop=stop")
        if not stop < limit:
            raise StateStoreError("protection stop must be below its target limit")
    elif shape not in (("market", "opg", "simple"), ("market", "day", "simple")) or (
        limit is not None or stop is not None
    ):
        raise StateStoreError(f"a {intent.kind} order is a simple market opg/day sell")


def _validate_intent(intent: OrderIntent) -> dict[str, str]:
    if not isinstance(intent, OrderIntent):
        raise StateStoreError("intent must be an OrderIntent")
    _client_id(intent.client_order_id)
    _symbol(intent.symbol)
    if intent.kind not in ORDER_KINDS:
        raise StateStoreError(f"unknown order kind {intent.kind!r}")
    if not isinstance(intent.side, OrderSide):
        raise StateStoreError("side must be an OrderSide")
    _positive_int(intent.qty, "qty")
    if intent.order_type not in ("market", "limit"):
        raise StateStoreError(f"unknown order_type {intent.order_type!r}")
    if intent.time_in_force not in ("opg", "day", "gtc"):
        raise StateStoreError(f"unknown time_in_force {intent.time_in_force!r}")
    if intent.order_class not in ("simple", "oco"):
        raise StateStoreError(f"unknown order_class {intent.order_class!r}")
    limit = _opt_price(intent.limit_price, "limit_price")
    stop = _opt_price(intent.stop_price, "stop_price")
    if intent.kind == "entry":
        _check_entry_shape(intent, limit, stop)
    else:
        _check_sell_shape(intent, limit, stop)
    return {
        "decision_session": date_text(intent.decision_session, "decision_session"),
        "valid_until": ts_text(intent.valid_until, "valid_until"),
        "created_at": ts_text(intent.created_at, "created_at"),
    }
