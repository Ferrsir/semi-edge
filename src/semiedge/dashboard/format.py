"""Display formatting for dashboard tables (F-168..F-174). Pure functions, no UI library.

Every value still passes through ``display`` afterwards, so formatting never decides what is
safe to show. Missing values stay visibly missing (``MISSING``); nothing is filled in.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Any

MISSING = "—"

SECTION_LABELS = {
    "active_halts": "Active halts",
    "equity": "Equity by session",
    "heartbeats": "Heartbeats",
    "latest_scan": "Latest scan",
    "ranked_evaluations": "Ranked evaluations",
    "orders": "Orders",
    "fills": "Fills",
    "reconciliation": "Reconciliation",
    "failed_runs": "Failed runs",
    "outbox_errors": "Notification errors",
}

COLUMN_LABELS = {
    "ts": "Time",
    "session": "Session",
    "equity": "Equity",
    "last_equity": "Previous close",
    "cash": "Cash",
    "long_market_value": "Long value",
    "source": "Source",
    "run_id": "Run",
    "symbol": "Symbol",
    "eligible": "Eligible",
    "score": "Score",
    "rank": "Rank",
    "close": "Close",
    "atr": "ATR",
    "proposed_stop": "Stop",
    "proposed_target": "Target",
    "rejection_reasons": "Rejected because",
    "component": "Component",
    "age_seconds": "Last report",
    "status": "Status",
    "freshness": "Freshness",
    "detail": "Detail",
    "qty": "Qty",
    "avg_entry_price": "Avg entry",
    "entry_session": "Entry session",
    "held_sessions": "Sessions held",
    "held_sessions_as_of": "As of",
    "stop": "Stop",
    "target": "Target",
}

_MONEY = frozenset(
    {
        "equity",
        "last_equity",
        "cash",
        "long_market_value",
        "avg_entry_price",
        "entry_reference",
        "stop",
        "target",
        "close",
        "price",
        "filled_avg_price",
        "proposed_stop",
        "proposed_target",
        "stop_price",
        "limit_price",
    }
)
_DECIMAL = frozenset({"score", "atr", "split_ratio"})
_INTEGER = frozenset({"rank", "qty", "attempts", "held_sessions", "filled_qty"})
_BOOLEAN = frozenset({"eligible"})
_SECONDS = frozenset({"age_seconds"})
_PAYLOAD_FIELDS = ("close", "atr", "proposed_stop", "proposed_target", "rejection_reasons")
_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and value.strip() == ""


def age_text(seconds: float) -> str:
    """``45 s``, ``22 min``, ``3 h 05 min``, ``2 d 4 h``."""
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{seconds:.0f} s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} min"
    hours, rest = divmod(int(minutes), 60)
    if hours < 48:
        return f"{hours} h {rest:02d} min"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h"


def _time_text(value: datetime) -> str:
    if value.tzinfo is None:
        return value.strftime("%Y-%m-%d %H:%M")
    return value.strftime("%Y-%m-%d %H:%M %Z").replace("+00:00", "UTC").strip()


def _parse_time(value: str) -> datetime | None:
    if "T" not in value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def cell(column: str, value: object) -> object:  # noqa: PLR0911 - one branch per column kind
    """Human-readable form of one table value; unknown kinds pass through unchanged."""
    if is_missing(value):
        return MISSING
    if column in _BOOLEAN:
        return "yes" if value in (True, 1, "1") else "no" if value in (False, 0, "0") else value
    if column in _SECONDS and isinstance(value, int | float):
        return age_text(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if column in _MONEY and isinstance(value, int | float):
        return f"{value:,.2f}"
    if column in _DECIMAL and isinstance(value, int | float):
        return f"{value:,.2f}"
    if column in _INTEGER and isinstance(value, int | float) and float(value).is_integer():
        return f"{int(value):,}"
    if isinstance(value, datetime):
        return _time_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and (parsed := _parse_time(value)) is not None:
        return _time_text(parsed)
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in value) if value else MISSING
    return value


def expand_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Replace a signal's ``payload_json`` blob with the few fields an operator reads (F-170)."""
    if "payload_json" not in row:
        return row
    out = {k: v for k, v in row.items() if k != "payload_json"}
    try:
        payload = json.loads(str(row["payload_json"]))
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        for key in _PAYLOAD_FIELDS:
            out[key] = "unavailable: payload unreadable"
        return out
    for key in _PAYLOAD_FIELDS:
        out[key] = payload.get(key)
    return out


def table_rows(rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    """Expand payloads, format cells and rename columns; column order follows the first row."""
    expanded = [expand_payload(dict(r)) for r in rows]
    return [
        {
            COLUMN_LABELS.get(str(k), str(k).replace("_", " ").capitalize()): cell(str(k), v)
            for k, v in r.items()
        }
        for r in expanded
    ]


def safe_component(name: object) -> str:
    """A heartbeat component name that is safe inside a Markdown-rendering alert."""
    text = str(name)
    return text if _COMPONENT.match(text) else "unnamed component"


def stale_message(heartbeat: dict[str, Any]) -> str:
    """Alert text with the component, its age and its interval in one place (F-172)."""
    interval = None
    try:
        metadata = json.loads(str(heartbeat.get("detail", "")))
        if isinstance(metadata, dict):
            interval = metadata.get("interval_seconds")
    except ValueError:
        pass
    every = f", expected every {age_text(interval)}" if isinstance(interval, int | float) else ""
    return (
        f"Stale heartbeat: {safe_component(heartbeat.get('component'))} last reported "
        f"{age_text(float(heartbeat.get('age_seconds', 0)))} ago{every}."
    )


def summary_rows(
    sections: dict[str, Any], *, positions: list[dict[str, Any]], last_run: tuple[str, str]
) -> list[dict[str, str]]:
    """Field/value rows for the Overview glance table (F-174). Absent data says so."""
    equity = sections.get("equity") or []
    latest = equity[-1] if equity else None
    if latest is None:
        equity_text = change_text = "unavailable: no equity snapshot"
    else:
        equity_text = str(cell("equity", latest.get("equity")))
        now, prev = latest.get("equity"), latest.get("last_equity")
        if isinstance(now, int | float) and isinstance(prev, int | float) and prev:
            change_text = f"{now - prev:+,.2f} ({(now / prev - 1):+.2%})"
        else:
            change_text = "unavailable: previous close not recorded"
    open_positions = sum(1 for p in positions if p.get("status") in {"open", "exiting"})
    halts = sections.get("active_halts") or []
    return [
        {"Field": "Equity", "Value": equity_text},
        {"Field": "Change since previous close", "Value": change_text},
        {"Field": "Open positions", "Value": str(open_positions)},
        {"Field": "Active halts", "Value": str(len(halts))},
        {"Field": "Last run", "Value": last_run[0]},
        {"Field": "Last run started", "Value": last_run[1]},
    ]


def equity_chart_spec(
    equity: list[dict[str, Any]], *, color: str, min_points: int
) -> dict[str, Any] | None:
    """Vega-Lite spec for the equity line, or ``None`` when there are too few points.

    A plain dict, so building and testing it needs neither Streamlit nor Altair (F-176). The x
    field is the full ISO session date, so two sessions never merge under one label (F-175);
    the axis only formats it as ``Mon DD``. The y axis is zoomed to the data, not pinned at 0.
    """
    values = [
        {"session": str(row["session"]), "equity": float(row["equity"])}
        for row in equity
        if isinstance(row.get("equity"), int | float) and not isinstance(row.get("equity"), bool)
    ]
    if len(values) < min_points:
        return None
    return {
        "data": {"values": values},
        "height": 230,
        "mark": {"type": "line", "color": color, "strokeWidth": 2, "point": {"size": 40}},
        "encoding": {
            "x": {
                "field": "session",
                "type": "ordinal",
                "sort": None,
                "title": None,
                "axis": {
                    "labelAngle": 0,
                    "labelExpr": "timeFormat(timeParse(datum.value, '%Y-%m-%d'), '%b %d')",
                },
            },
            "y": {
                "field": "equity",
                "type": "quantitative",
                "title": None,
                "scale": {"zero": False},
                "axis": {"format": ",.0f"},
            },
            "tooltip": [
                {"field": "session", "type": "ordinal", "title": "Session"},
                {"field": "equity", "type": "quantitative", "title": "Equity", "format": ",.2f"},
            ],
        },
    }
