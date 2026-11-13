"""Human halt control that works while the engine runs (spec §5.1, §8; issue #21).

* :func:`request_manual_halt` is the ONLY write available to the dashboard: one INSERT into
  ``halts`` with ``kind='manual'`` and ``created_by='dashboard:<actor>'``.
* :func:`clear_halt` is for the CLI only (``semiedge halt clear``; ``cleared_by='cli:<actor>'``).
  A static safety test asserts that ``semiedge.dashboard`` never references it.

Neither function creates or migrates a database (a missing file is a :class:`StateStoreError`)
and neither takes the writer lock: WAL plus ``busy_timeout`` serialise them with the engine.
This module deliberately does not import ``semiedge.state.store``.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Final

from semiedge.state._reader import StateStoreError, read_identity, text, ts_text

__all__ = ["clear_halt", "request_manual_halt"]

_ACTOR: Final = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_MAX_REASON: Final = 2000


def _actor_name(actor: object) -> str:
    """A bare actor name; the prefix (``dashboard:``/``cli:``) is added here, never by callers."""
    if type(actor) is not str or _ACTOR.fullmatch(actor) is None:
        raise StateStoreError(f"actor must be a bare name matching {_ACTOR.pattern}")
    return actor


@contextmanager
def _write_connection(path: Path) -> Iterator[sqlite3.Connection]:
    if not isinstance(path, Path):
        raise StateStoreError("path must be a pathlib.Path")
    if not path.is_file():
        raise StateStoreError(f"state database not found: {path}")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True, isolation_level=None)
    except sqlite3.Error as exc:
        raise StateStoreError(f"cannot open state database: {exc}") from None
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            read_identity(conn)  # refuses unknown/newer schemas before writing anything
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    except sqlite3.Error as exc:
        raise StateStoreError(f"state database error: {exc}") from exc
    finally:
        conn.close()


def request_manual_halt(path: Path, reason: str, actor: str, now: datetime) -> int:
    """Insert one MANUAL halt row (idempotent: an active MANUAL halt returns its id)."""
    reason = text(reason, "reason", max_len=_MAX_REASON)
    created_by = f"dashboard:{_actor_name(actor)}"
    ts = ts_text(now, "now")
    with _write_connection(path) as conn:
        row = conn.execute(
            "SELECT id FROM halts WHERE kind = 'manual' AND cleared_at IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        if row is not None:
            return int(row[0])
        cursor = conn.execute(
            "INSERT INTO halts (kind, reason, created_at, created_by, cleared_at, cleared_by) "
            "VALUES ('manual', ?, ?, ?, NULL, NULL)",
            (reason, ts, created_by),
        )
        if cursor.lastrowid is None:  # pragma: no cover - sqlite always sets it for INSERT
            raise StateStoreError("halt insert returned no row id")
        return cursor.lastrowid


def clear_halt(path: Path, halt_id: int, actor: str, now: datetime) -> None:
    """CLI only: set ``cleared_at``/``cleared_by='cli:<actor>'`` on one active halt (any kind)."""
    if type(halt_id) is not int:
        raise StateStoreError("halt_id must be an int")
    cleared_by = f"cli:{_actor_name(actor)}"
    ts = ts_text(now, "now")
    with _write_connection(path) as conn:
        cursor = conn.execute(
            "UPDATE halts SET cleared_at = ?, cleared_by = ? WHERE id = ? AND cleared_at IS NULL",
            (ts, cleared_by, halt_id),
        )
        if cursor.rowcount != 1:
            raise StateStoreError(f"halt {halt_id} is not an active halt")
