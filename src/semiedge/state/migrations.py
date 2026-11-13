# ruff: noqa: E501  (MIGRATION_001 is the spec text verbatim, including its long lines)
"""Schema migrations for the SQLite state store (spec phase2-4-interfaces.md §5.2; issue #21).

``MIGRATION_001`` is the §5.2 SQL block **verbatim** (a test compares it with the specification
text). Migrations are forward only, numbered, idempotent and applied inside one transaction by
:class:`semiedge.state.store.StateStore`; an unknown higher ``schema_version`` is refused.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

__all__ = ["LATEST_SCHEMA_VERSION", "MIGRATIONS", "MIGRATION_001", "split_statements"]

MIGRATION_001: Final = """CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);         -- store_id, created_at, data_source ('real'|'synthetic')
CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY, kind TEXT NOT NULL,          -- 'engine'|'post_close'|'pre_open'|'monitor'|'reconcile'
  started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL,   -- 'running'|'ok'|'failed'|'aborted'|'skipped'
  mode TEXT NOT NULL, git_sha TEXT, config_fingerprint TEXT NOT NULL, detail TEXT);

CREATE TABLE signals (
  run_id TEXT NOT NULL REFERENCES runs, session TEXT NOT NULL, symbol TEXT NOT NULL,
  eligible INTEGER NOT NULL, score REAL, rank INTEGER, payload_json TEXT NOT NULL,
  PRIMARY KEY (run_id, symbol));

CREATE TABLE risk_decisions (
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs, decided_at TEXT NOT NULL,
  session TEXT NOT NULL, symbol TEXT NOT NULL, approved INTEGER NOT NULL,
  proposed_shares INTEGER NOT NULL, approved_shares INTEGER NOT NULL,
  reasons_json TEXT NOT NULL, checks_json TEXT NOT NULL, limits_fingerprint TEXT NOT NULL,
  proposal_json TEXT NOT NULL);                         -- full EntryProposal (levels, rank, score, atr)

CREATE TABLE orders (
  client_order_id TEXT PRIMARY KEY,                     -- §6.2
  broker_order_id TEXT UNIQUE,
  risk_decision_id INTEGER REFERENCES risk_decisions,   -- NOT NULL for kind='entry' (CHECK below)
  parent_client_order_id TEXT REFERENCES orders,
  symbol TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  kind TEXT NOT NULL CHECK (kind IN ('entry','protection','time_exit','flatten')),
  order_type TEXT NOT NULL CHECK (order_type IN ('market','limit')),
  time_in_force TEXT NOT NULL CHECK (time_in_force IN ('opg','day','gtc')),
  order_class TEXT NOT NULL CHECK (order_class IN ('simple','oco')),
  qty INTEGER NOT NULL CHECK (qty > 0), limit_price REAL, stop_price REAL,
  decision_session TEXT NOT NULL, valid_until TEXT NOT NULL,   -- UTC; intent expires unsubmitted after this
  status TEXT NOT NULL,                                 -- §6.4
  filled_qty INTEGER NOT NULL DEFAULT 0 CHECK (filled_qty >= 0 AND filled_qty <= qty),
  filled_avg_price REAL, legs_json TEXT NOT NULL DEFAULT '[]',   -- OCO leg broker ids/status
  created_at TEXT NOT NULL, submitted_at TEXT, updated_at TEXT NOT NULL, last_error TEXT,
  CHECK (kind <> 'entry' OR (risk_decision_id IS NOT NULL AND side = 'buy')),
  CHECK (kind = 'entry' OR side = 'sell'),
  CHECK (kind <> 'protection' OR (order_class = 'oco' AND limit_price IS NOT NULL AND stop_price IS NOT NULL)));

CREATE TABLE order_events (
  id INTEGER PRIMARY KEY, client_order_id TEXT NOT NULL REFERENCES orders, ts TEXT NOT NULL,
  event TEXT NOT NULL, from_status TEXT, to_status TEXT, broker_status TEXT,
  filled_qty INTEGER, price REAL, detail_json TEXT NOT NULL);    -- redacted (§13)

CREATE TABLE fills (                                   -- §6.4 "Fill ingestion" (F-038)
  fill_key TEXT PRIMARY KEY,                            -- broker activity id, else f"{client_order_id}:{cum_qty}"
  client_order_id TEXT NOT NULL REFERENCES orders, ts TEXT NOT NULL,
  qty INTEGER NOT NULL CHECK (qty > 0), price REAL NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('activity','cumulative')),
  status TEXT NOT NULL CHECK (status IN ('final','provisional','superseded')),
  cum_qty INTEGER NOT NULL CHECK (cum_qty > 0),         -- this order's local cumulative after ingestion
  superseded_at TEXT,
  CHECK (source = 'activity' OR status IN ('provisional','superseded')),
  CHECK (source = 'cumulative' OR status = 'final'),
  CHECK ((status = 'superseded') = (superseded_at IS NOT NULL)));

CREATE TABLE corporate_action_adjustments (            -- §9.4 (F-039); append-only
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, ex_date TEXT NOT NULL,
  event_id TEXT NOT NULL, new_shares INTEGER NOT NULL CHECK (new_shares > 0),
  old_shares INTEGER NOT NULL CHECK (old_shares > 0 AND old_shares <> new_shares),
  qty_before INTEGER NOT NULL CHECK (qty_before > 0), qty_after INTEGER NOT NULL CHECK (qty_after > 0),
  applied_at TEXT NOT NULL,
  UNIQUE (symbol, event_id));

CREATE TABLE positions (
  symbol TEXT PRIMARY KEY, qty INTEGER NOT NULL CHECK (qty >= 0),
  avg_entry_price REAL NOT NULL, entry_session TEXT NOT NULL,
  entry_client_order_id TEXT NOT NULL REFERENCES orders,
  entry_reference REAL NOT NULL, atr REAL NOT NULL, stop REAL NOT NULL, target REAL NOT NULL,
  split_ratio REAL NOT NULL DEFAULT 1.0,                -- product of applied adjustment ratios (§9.4)
  status TEXT NOT NULL CHECK (status IN ('open','exiting','closed')),
  opened_at TEXT NOT NULL, closed_at TEXT, updated_at TEXT NOT NULL);

CREATE TABLE halts (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('manual','daily_loss','drawdown','reconciliation','broker','data','protection')),
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,   -- 'engine'|'dashboard:<actor>'|'cli:<actor>'
  cleared_at TEXT, cleared_by TEXT);

CREATE TABLE reconciliation_runs (
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
  outcome TEXT,                                         -- 'clean'|'resolved'|'halted'|'error'
  discrepancies_json TEXT NOT NULL DEFAULT '[]');

CREATE TABLE equity_snapshots (
  ts TEXT PRIMARY KEY, session TEXT NOT NULL, equity REAL NOT NULL, last_equity REAL NOT NULL,
  cash REAL NOT NULL, long_market_value REAL NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('broker_close','broker_intraday')));

CREATE TABLE heartbeats (
  component TEXT PRIMARY KEY, ts TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL);

CREATE TABLE notifications_outbox (
  id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, category TEXT NOT NULL, mode_tag TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL, sent_at TEXT, last_error TEXT);
"""

MIGRATIONS: Final[Mapping[int, str]] = MappingProxyType({1: MIGRATION_001})
LATEST_SCHEMA_VERSION: Final = max(MIGRATIONS)


def split_statements(script: str) -> tuple[str, ...]:
    """Split a migration into complete statements (comments kept), so that no statement runs
    through ``executescript`` (which would COMMIT the surrounding transaction)."""
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise ValueError("migration script ends with an incomplete statement")
    return tuple(statements)
