"""Schema, PRAGMAs, migrations, writer lock and read-only access (spec §5.2-§5.4; issue #21)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from semiedge.state import halt_control
from semiedge.state.migrations import LATEST_SCHEMA_VERSION, MIGRATION_001, split_statements
from semiedge.state.readonly import ReadOnlyStateStore
from semiedge.state.store import LOCK_FILE_NAME, StateStore, StateStoreError
from state_support import T0, snapshot

pytestmark = pytest.mark.integration

SPEC = Path(__file__).resolve().parents[3] / "docs" / "specifications" / "phase2-4-interfaces.md"


def _spec_sql() -> str:
    doc = SPEC.read_text(encoding="utf-8")
    section = doc.index("### 5.2 Schema (migration 001")
    start = doc.index("```sql\n", section) + len("```sql\n")
    return doc[start : doc.index("```", start)]


def _structure(conn: sqlite3.Connection) -> dict[str, object]:
    tables = [
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    ]
    return {
        "master": conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall(),
        "columns": {t: conn.execute(f"PRAGMA table_xinfo({t})").fetchall() for t in tables},
        "foreign_keys": {
            t: conn.execute(f"PRAGMA foreign_key_list({t})").fetchall() for t in tables
        },
        "indexes": {t: conn.execute(f"PRAGMA index_list({t})").fetchall() for t in tables},
    }


def test_migration_001_is_the_spec_text_verbatim() -> None:
    assert _spec_sql() == MIGRATION_001
    assert LATEST_SCHEMA_VERSION == 1


def test_fresh_db_schema_matches_migration_001_exactly(
    db_path: Path, open_store: Callable[..., StateStore]
) -> None:
    open_store().close()
    reference = sqlite3.connect(":memory:")
    reference.executescript(_spec_sql())  # independent path: the spec text, run as a script
    actual = sqlite3.connect(db_path)
    try:
        assert _structure(actual) == _structure(reference)
        assert actual.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
        meta = dict(actual.execute("SELECT key, value FROM meta").fetchall())
        assert set(meta) == {"store_id", "created_at", "data_source"}
        assert meta["data_source"] == "real"
        assert meta["created_at"] == T0.isoformat(timespec="microseconds")
        assert len(meta["store_id"]) == 32
        int(meta["store_id"], 16)
    finally:
        actual.close()
        reference.close()


def test_pragmas_and_statement_split(db_path: Path, open_store: Callable[..., StateStore]) -> None:
    store = open_store()
    conn = store._conn
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert len(split_statements(MIGRATION_001)) == 15
    with pytest.raises(ValueError, match="incomplete"):
        split_statements("CREATE TABLE x (a")


def test_reopening_is_idempotent_and_keeps_store_id(
    db_path: Path, open_store: Callable[..., StateStore]
) -> None:
    first = open_store()
    store_id = first.store_id
    first.close()
    before = snapshot(db_path)
    second = open_store()
    assert second.store_id == store_id
    second.close()
    assert snapshot(db_path) == before


def test_second_writer_is_refused_and_lock_released_on_close(
    db_path: Path, open_store: Callable[..., StateStore]
) -> None:
    first = open_store()
    assert (db_path.parent / LOCK_FILE_NAME).exists()
    with pytest.raises(StateStoreError, match="writer_lock_held"):
        open_store()
    first.close()
    first.close()  # idempotent
    open_store().close()


def test_closed_store_refuses_use(open_store: Callable[..., StateStore]) -> None:
    store = open_store()
    store.close()
    with pytest.raises(StateStoreError, match="closed"):
        store.active_halts()
    with pytest.raises(StateStoreError, match="closed"):
        _ = store.store_id


def test_newer_schema_version_is_refused(
    db_path: Path, open_store: Callable[..., StateStore]
) -> None:
    open_store().close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (2, 'x')")
    conn.close()
    with pytest.raises(StateStoreError, match="newer than this binary"):
        open_store()
    with pytest.raises(StateStoreError, match="newer than this binary"):
        ReadOnlyStateStore(db_path)
    with pytest.raises(StateStoreError, match="newer than this binary"):
        halt_control.request_manual_halt(db_path, "stop", "owner", T0)
    # the refused writer released its lock, so a fixed DB opens again
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM schema_version WHERE version = 2")
    conn.close()
    open_store().close()


def test_data_source_mismatch_and_foreign_files_are_refused(
    tmp_path: Path, db_path: Path, open_store: Callable[..., StateStore]
) -> None:
    open_store(data_source="synthetic").close()
    with pytest.raises(StateStoreError, match="holds 'synthetic' data"):
        open_store(data_source="real")
    with pytest.raises(StateStoreError, match="data_source"):
        open_store(data_source="paper")
    foreign = tmp_path / "other" / "x.sqlite3"
    foreign.parent.mkdir()
    with sqlite3.connect(foreign) as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
    conn.close()
    with pytest.raises(StateStoreError, match="not a SEMI-EDGE state database"):
        open_store(path=foreign)
    with pytest.raises(StateStoreError, match="not a SEMI-EDGE state database"):
        ReadOnlyStateStore(foreign)


def test_readonly_store_cannot_write_and_never_creates(
    tmp_path: Path, db_path: Path, open_store: Callable[..., StateStore]
) -> None:
    missing = tmp_path / "nope" / "semiedge.sqlite3"
    with pytest.raises(StateStoreError, match="not found"):
        ReadOnlyStateStore(missing)
    assert not missing.parent.exists()
    writer = open_store()
    reader = ReadOnlyStateStore(db_path)  # works while the writer holds the lock
    try:
        assert reader.store_id == writer.store_id
        assert reader.data_source() == writer.data_source() == "real"
        with pytest.raises(sqlite3.OperationalError):
            reader._conn.execute(
                "INSERT INTO halts (kind, reason, created_at, created_by) "
                "VALUES ('manual', 'x', 'y', 'z')"
            )
        with pytest.raises(sqlite3.OperationalError):
            reader._conn.execute("CREATE TABLE evil (a)")
        assert writer.active_halts() == ()
    finally:
        reader.close()
        reader.close()
    with pytest.raises(StateStoreError, match="closed"):
        reader.orders()


def test_halt_control_never_creates_a_database(tmp_path: Path) -> None:
    missing = tmp_path / "state" / "semiedge.sqlite3"
    with pytest.raises(StateStoreError, match="not found"):
        halt_control.request_manual_halt(missing, "stop", "owner", T0)
    with pytest.raises(StateStoreError, match="not found"):
        halt_control.clear_halt(missing, 1, "owner", T0)
    assert not missing.exists()
    assert not missing.parent.exists()


def test_constructor_rejects_bad_arguments(tmp_path: Path) -> None:
    with pytest.raises(StateStoreError, match="pathlib"):
        StateStore("x.sqlite3", data_source="real", now=T0)  # type: ignore[arg-type]
    with pytest.raises(StateStoreError, match="timezone-aware"):
        StateStore(tmp_path / "s" / "x.sqlite3", data_source="real", now=T0.replace(tzinfo=None))
    assert not (tmp_path / "s").exists()  # refused before touching the filesystem
    with pytest.raises(StateStoreError, match="pathlib"):
        ReadOnlyStateStore("x.sqlite3")  # type: ignore[arg-type]
