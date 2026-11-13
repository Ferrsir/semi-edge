"""Testable local-data projection. No UI library, credentials or broker access."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from semiedge.backtest.results import BacktestResult
from semiedge.dashboard.desk import Desk, collect_desk
from semiedge.dashboard.format import cell
from semiedge.data.calendar import TradingCalendar
from semiedge.reporting import SYNTHETIC_BANNER
from semiedge.security.untrusted import UntrustedText
from semiedge.state.models import TERMINAL_STATUSES
from semiedge.state.readonly import ReadOnlyStateStore
from semiedge.strategy.exits import held_sessions

Source = Literal["cache", "synthetic"]


def source_root(data_dir: Path, source: Source) -> Path:
    if source not in {"cache", "synthetic"}:
        raise ValueError("source must be cache or synthetic")
    return data_dir / "synthetic" if source == "synthetic" else data_dir


def display(value: object) -> str:
    """Clean external text for plain-text widgets; the desk also HTML-escapes it (#108)."""
    return UntrustedText.from_raw("file", str(value)).text


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{str(k): v for k, v in row.items()} for row in frame.to_dict(orient="records")]


@dataclass(frozen=True)
class DashboardView:
    source: str
    mode: str
    sections: dict[str, Any]
    last_run: tuple[str, str] = ("unavailable: no recorded run", "unavailable: no recorded run")
    desk: Desk | None = None


def heartbeat_freshness(detail: str, age: float) -> str:
    """Use an interval only when explicitly persisted in heartbeat JSON metadata."""
    try:
        metadata = json.loads(detail)
    except ValueError:
        return "unavailable: interval not recorded"
    if not isinstance(metadata, dict) or "interval_seconds" not in metadata:
        return "unavailable: interval not recorded"
    interval = metadata["interval_seconds"]
    if type(interval) not in {int, float} or not math.isfinite(interval) or interval <= 0:
        raise ValueError("invalid persisted heartbeat interval")
    return "stale" if age > 3 * interval else "fresh"


def load_view(path: Path, *, source: Source, now: datetime) -> DashboardView:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if source not in {"cache", "synthetic"}:
        raise ValueError("source must be cache or synthetic")
    store = ReadOnlyStateStore(path)
    try:
        actual = store.data_source()
        if actual != ("synthetic" if source == "synthetic" else "real"):
            raise ValueError("database source does not match selected source")
        runs = _records(store.recent_runs())
        mode = str(runs[0]["mode"]).upper() if runs else "UNAVAILABLE: no recorded run"
        if runs and mode not in {"SCANNER", "BACKTEST", "PAPER"}:
            raise ValueError("unsupported persisted run mode")
        scan = store.latest_signal_run_id()
        equity = _records(store.equity_snapshots())
        heartbeats = []
        for component, ts, status, detail in store.heartbeats():
            age = (now - ts).total_seconds()
            if age < 0:
                raise ValueError("heartbeat timestamp is in the future")
            heartbeats.append(
                {
                    "component": component,
                    "age_seconds": age,
                    "status": status,
                    "detail": detail,
                    "freshness": heartbeat_freshness(detail, age),
                }
            )
        orders = store.orders(limit=500)
        protection = store.orders(kinds=["protection"])
        positions = []
        for position in store.positions():
            row = asdict(position)
            row["held_sessions"] = "unavailable: decision session not recorded"
            if equity:
                session = datetime.fromisoformat(str(equity[-1]["session"])).date()
                if session >= position.entry_session:
                    row["held_sessions"] = held_sessions(
                        position.entry_session, session, TradingCalendar()
                    )
                    row["held_sessions_as_of"] = session
            row["protection_status"] = [
                {
                    "status": order.status,
                    "stop": order.intent.stop_price,
                    "target": order.intent.limit_price,
                    "qty": order.intent.qty,
                }
                for order in protection
                if order.intent.symbol == position.symbol and order.status not in TERMINAL_STATUSES
            ] or "unavailable: no protection order recorded"
            positions.append(row)
        last_run = ("unavailable: no recorded run", "unavailable: no recorded run")
        if runs:
            latest = runs[0]
            last_run = (
                f"{latest['kind']}, {latest['status']}",
                str(cell("ts", latest["started_at"])),
            )
        signal_rows = [] if scan is None else _records(store.signals(scan))
        desk = collect_desk(
            store,
            mode=mode,
            source=actual,
            scan=scan,
            signals=signal_rows,
            equity=equity,
            heartbeats=heartbeats,
            positions=positions,
            last_run=last_run,
        )
        return DashboardView(
            actual,
            mode,
            {
                "Overview": {
                    "active_halts": [asdict(h) for h in store.active_halts()],
                    "equity": equity,
                    "heartbeats": heartbeats,
                },
                "Signals": {
                    "latest_scan": scan,
                    "ranked_evaluations": signal_rows,
                },
                "Positions": positions,
                "Trade history": {
                    "orders": [asdict(o) for o in orders],
                    "fills": [asdict(f) for f in store.fills(limit=500)],
                },
                "Operations": {
                    "reconciliation": [asdict(r) for r in store.reconciliation_history(100)],
                    "failed_runs": [r for r in runs if r["status"] == "failed"],
                    "outbox_errors": _records(store.outbox_errors(100)),
                },
            },
            last_run,
            desk,
        )
    finally:
        store.close()


def _finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite report number")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    if isinstance(value, list):
        for item in value:
            _finite(item)


def load_backtests(root: Path, *, source: Source) -> list[dict[str, Any]]:
    """Read only backtest artifact files from this source's report directory."""
    reports = []
    directory = root / "reports" / "backtests"
    for path in sorted(directory.glob("*.json")):
        if path.resolve().parent != directory.resolve() or path.stat().st_size > 20_000_000:
            raise ValueError("invalid report path or oversized report")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("unsupported backtest artifact schema")
        if set(raw) != {field.name for field in fields(BacktestResult)}:
            raise ValueError("incomplete backtest artifact")
        labels = raw.get("labels")
        if (
            not isinstance(labels, list)
            or not labels
            or not all(isinstance(x, str) for x in labels)
        ):
            raise ValueError("backtest labels missing")
        if (SYNTHETIC_BANNER in labels) != (source == "synthetic"):
            raise ValueError("backtest source mismatch")
        if not {
            "SURVIVORSHIP-BIASED UNIVERSE: current hand-picked list, not point-in-time",
            "PRICE RETURN: dividends excluded",
        }.issubset(labels):
            raise ValueError("backtest mandatory labels missing")
        if not isinstance(raw.get("command"), str) or not raw["command"].strip():
            raise ValueError("backtest reproducing command missing")
        for key in ("trades", "equity_curve", "risk_decisions", "halts", "data_gaps"):
            if not isinstance(raw.get(key), list):
                raise ValueError("incomplete backtest artifact")
        _finite(raw)
        reports.append(raw)
    return reports
