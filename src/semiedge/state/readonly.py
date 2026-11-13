"""Read-only view of the state database for the dashboard and health checks (§5.1; issue #21).

``ReadOnlyStateStore`` opens the file with ``mode=ro`` and ``PRAGMA query_only=ON``; it never
creates, migrates or locks a database, so it works while the engine (the single writer) runs.
It implements :class:`semiedge.state.models.StateReader` through the same base class as
``StateStore`` (identical signatures, SQL and decoding; F-037).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from semiedge.state._reader import (
    StateReaderBase,
    StateStoreError,
    decode_reconciliation,
    read_identity,
)
from semiedge.state.models import ReconciliationReport

__all__ = ["ReadOnlyStateStore"]


def _check_limit(limit: object) -> int:
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be an int > 0")
    return limit


def _ts(value: object) -> datetime:
    if type(value) is not str:
        raise StateStoreError("stored timestamp is not text")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise StateStoreError("stored timestamp has no UTC offset")
    return parsed.astimezone(UTC)


def open_readonly(path: Path) -> sqlite3.Connection:
    """A read-only connection to an existing file (missing file -> StateStoreError)."""
    if not isinstance(path, Path):
        raise StateStoreError("path must be a pathlib.Path")
    if not path.is_file():
        raise StateStoreError(f"state database not found: {path}")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error as exc:
        raise StateStoreError(f"cannot open state database read-only: {exc}") from None
    return conn


class ReadOnlyStateStore(StateReaderBase):
    """Implements ``StateReader`` (same signatures as ``StateStore``) plus dashboard reads."""

    def __init__(self, path: Path) -> None:
        conn = open_readonly(path)
        try:
            self._store_id, self._data_source = read_identity(conn)
        except BaseException:
            conn.close()
            raise
        self._conn = conn

    def close(self) -> None:
        conn = getattr(self, "_conn", None)
        if conn is not None:
            del self._conn
            conn.close()

    def heartbeats(self) -> tuple[tuple[str, datetime, str, str], ...]:
        """(component, ts, status, detail), ordered by component."""
        rows = self._connection().execute(
            "SELECT component, ts, status, detail FROM heartbeats ORDER BY component"
        )
        return tuple((str(c), _ts(ts), str(s), str(d)) for c, ts, s, d in rows)

    def latest_run(self, kind: str) -> tuple[str, str, datetime, datetime | None, str] | None:
        """(run_id, status, started_at, ended_at, detail) of the newest run of ``kind``."""
        row = (
            self._connection()
            .execute(
                "SELECT run_id, status, started_at, ended_at, detail FROM runs WHERE kind = ? "
                "ORDER BY started_at DESC, run_id DESC LIMIT 1",
                (kind,),
            )
            .fetchone()
        )
        if row is None:
            return None
        run_id, status, started, ended, detail = row
        return (
            str(run_id),
            str(status),
            _ts(started),
            None if ended is None else _ts(ended),
            "" if detail is None else str(detail),
        )

    def signals(self, run_id: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT run_id, session, symbol, eligible, score, rank, payload_json FROM signals "
            "WHERE run_id = ? ORDER BY rank IS NULL, rank, symbol",
            self._connection(),
            params=(run_id,),
        )

    def equity_snapshots(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT ts, session, equity, last_equity, cash, long_market_value, source "
            "FROM equity_snapshots ORDER BY ts",
            self._connection(),
        )

    def reconciliation_history(self, limit: int) -> tuple[ReconciliationReport, ...]:
        """The newest ``limit`` reconciliation runs, newest first."""
        lim = _check_limit(limit)
        rows = self._connection().execute(
            "SELECT started_at, finished_at, outcome, discrepancies_json FROM reconciliation_runs "
            "ORDER BY id DESC LIMIT ?",
            (lim,),
        )
        return tuple(decode_reconciliation(row) for row in rows)

    def outbox_errors(self, limit: int) -> pd.DataFrame:
        """Unsent notifications with a recorded error, newest first."""
        lim = _check_limit(limit)
        return pd.read_sql_query(
            "SELECT id, created_at, category, mode_tag, title, attempts, next_attempt_at, "
            "last_error FROM notifications_outbox WHERE sent_at IS NULL AND last_error IS NOT "
            "NULL ORDER BY id DESC LIMIT ?",
            self._connection(),
            params=(lim,),
        )
