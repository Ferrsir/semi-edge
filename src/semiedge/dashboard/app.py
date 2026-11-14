"""Thin Streamlit renderer; run through ``semiedge dashboard``.

External text reaches the page through plain-text widgets (``st.text``, dataframes) after
``display``, or through ``semiedge.dashboard.desk``, which HTML-escapes every value before it
builds the desk markup for ``st.html`` (#108). Widgets that render Markdown (``subheader``,
``caption``, ``warning``, ``error``) receive fixed strings, validated enums, numbers, or a
component name that passed ``safe_component``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from semiedge.dashboard.charts import (
    RANGES,
    cached_symbols,
    candle_rows,
    candle_spec,
    load_daily,
    summary,
)
from semiedge.dashboard.desk import render_desk
from semiedge.dashboard.format import SECTION_LABELS, stale_message, table_rows
from semiedge.dashboard.viewmodel import (
    display,
    load_backtests,
    load_view,
    source_root,
)
from semiedge.reporting import SYNTHETIC_BANNER
from semiedge.state.halt_control import request_manual_halt


def _plain(st: Any, value: object) -> None:
    text = json.dumps(value, default=str, indent=2, ensure_ascii=False)
    for offset in range(0, len(text), 1800):
        st.text(display(text[offset : offset + 1800]))


def _table(st: Any, rows: list[dict[str, Any]]) -> None:
    clean = [{display(k): display(v) for k, v in row.items()} for row in table_rows(rows)]
    st.dataframe(pd.DataFrame(clean), hide_index=True, width="stretch")


def _section(st: Any, value: object, *, label: str | None = None) -> None:
    if label is not None:
        st.subheader(SECTION_LABELS.get(label, "Details"))
    if isinstance(value, dict):
        for key, rows in value.items():
            _section(st, rows, label=str(key))
    elif isinstance(value, list) and value and all(isinstance(row, dict) for row in value):
        _table(st, value)
    elif value == [] or value is None:
        st.text("No records yet.")
    elif isinstance(value, str):
        st.text(display(value))
    else:
        _plain(st, value)


def _overview(st: Any, view: Any, root: Path, source: Any) -> None:
    section = view.sections["Overview"]
    for heartbeat in section["heartbeats"]:
        if heartbeat["freshness"] == "stale":
            st.error(stale_message(heartbeat))
    if view.desk is not None:
        st.html(render_desk(view.desk))
    st.subheader("Records")
    _section(st, section)
    try:
        reports = load_backtests(root, source=source)
    except (OSError, ValueError) as exc:
        st.subheader("Backtest reports")
        st.text("Backtest reports unavailable")
        st.text(display(exc))
        return
    if reports:
        st.subheader("Backtest reports")
    for report in reports:
        for label in report["labels"]:
            st.text(display(label))
        st.text(display(f"Command: {report['command']}"))
        _plain(st, report)


def _operations(st: Any, view: Any, path: Path, now: datetime | None) -> None:
    _section(st, view.sections["Operations"])
    st.subheader("Manual halt")
    st.text("A manual halt stops new entries. Exits and protective stops keep running.")
    with st.form("manual_halt"):
        actor = st.text_input("Actor")
        reason = st.text_input("Reason")
        confirmation = st.text_input("Type HALT to confirm")
        submitted = st.form_submit_button("Halt new entries")
    if submitted:
        if confirmation != "HALT" or not reason.strip():
            st.text("Not recorded: type HALT and give a reason.")
        else:
            try:
                halt_id = request_manual_halt(
                    path, display(reason), actor, now or datetime.now(UTC)
                )
            except (ValueError, RuntimeError) as exc:
                st.text(display(exc))
            else:
                st.text(f"Manual halt recorded: {halt_id}")


PAGES = ["Overview", "Signals", "Positions", "Trade history", "Operations", "Charts"]


def _charts(st: Any, root: Path) -> None:
    """Daily candles from the price cache (#116); needs no state database."""
    st.header("Charts")
    symbols = cached_symbols(root)
    if not symbols:
        st.text("No cached daily bars yet. Run `semiedge data fetch` to fill the cache.")
        return
    st.caption("Daily bars from the local cache · consolidated tape (SIP) · split-adjusted")
    default = symbols.index("SMH") if "SMH" in symbols else 0
    symbol = st.selectbox("Symbol", symbols, index=default)
    span = st.radio("Range", list(RANGES), index=1, horizontal=True)
    try:
        frame = load_daily(root, symbol)
    except (OSError, ValueError) as exc:
        st.text("Price data unavailable")
        st.text(display(exc))
        return
    if frame is None:
        st.text("No cached bars for this symbol.")
        return
    rows = candle_rows(frame, RANGES[span])
    spec = candle_spec(rows, title=symbol)
    if spec is None:
        st.text("No usable bars in this range.")
        return
    read = summary(rows)
    st.text(display(read["last"]))
    st.text(display(read["averages"]))
    st.vega_lite_chart(spec, width="stretch")
    st.caption("5-minute candles appear here once the intraday data is cached.")


def render(st: Any, *, source: Any = "cache", now: datetime | None = None) -> None:
    st.set_page_config(page_title="SEMI-EDGE", layout="wide")
    st.sidebar.text("SEMI-EDGE · PAPER only")
    root = source_root(Path(os.environ.get("SEMIEDGE_DATA_DIR", "data")), source)
    path = root / "state" / "semiedge.sqlite3"
    if source == "synthetic":
        st.warning(SYNTHETIC_BANNER)
    page = st.sidebar.radio("Page", PAGES)
    st.sidebar.button("Refresh")
    if page == "Charts":  # reads the price cache only, so it works before the bot has ever run
        _charts(st, root)
        return
    try:
        view = load_view(path, source=source, now=now or datetime.now(UTC))
    except (OSError, ValueError, RuntimeError) as exc:
        st.header("Dashboard unavailable")
        st.text(display(exc))
        if not path.exists():
            st.text("The bot has not run a session yet, so there is no state database.")
            st.text("The Charts page works from the price cache in the meantime.")
        return
    except Exception:
        st.header("Dashboard unavailable")
        st.text("The state database cannot be decoded.")
        return
    if page != "Overview" or view.desk is None:  # the desk carries its own header
        st.header(page)
        st.caption(f"Mode {view.mode} · data source {view.source}")
    if page == "Overview":
        _overview(st, view, root, source)
    elif page == "Operations":
        _operations(st, view, path, now)
    else:
        _section(st, view.sections[page])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("cache", "synthetic"), default="cache")
    args = parser.parse_args()
    render(importlib.import_module("streamlit"), source=args.source)


if __name__ == "__main__":
    main()
