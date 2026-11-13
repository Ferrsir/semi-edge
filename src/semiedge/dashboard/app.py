"""Thin Streamlit renderer; run through ``semiedge dashboard``."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

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


def _section(st: Any, value: object) -> None:
    if isinstance(value, dict):
        for key, rows in value.items():
            st.text(display(str(key).replace("_", " ").title()))
            _section(st, rows)
    elif isinstance(value, list) and value and all(isinstance(row, dict) for row in value):
        rows = [{display(k): display(v) for k, v in row.items()} for row in value]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    elif value == [] or value is None:
        st.text("No records available.")
    else:
        _plain(st, value)


def render(st: Any, *, source: Any = "cache", now: datetime | None = None) -> None:  # noqa: PLR0912

    st.set_page_config(page_title="SEMI-EDGE", layout="wide")
    st.title("SEMI-EDGE · PAPER only")
    root = source_root(Path(os.environ.get("SEMIEDGE_DATA_DIR", "data")), source)
    path = root / "state" / "semiedge.sqlite3"
    try:
        view = load_view(path, source=source, now=now or datetime.now(UTC))
    except (OSError, ValueError, RuntimeError) as exc:
        st.text("Dashboard unavailable")
        st.text(display(exc))
        return
    except Exception:
        st.text("Dashboard unavailable: state database cannot be decoded.")
        return
    st.text(display(f"Mode: {view.mode} | DB source: {view.source}"))
    if view.source == "synthetic":
        st.text(SYNTHETIC_BANNER)
    page = st.sidebar.radio("Page", list(view.sections))
    st.header(page)
    st.button("Refresh")
    if page == "Overview":
        for heartbeat in view.sections["Overview"]["heartbeats"]:
            if heartbeat["freshness"] == "stale":
                st.error("Stale heartbeat: more than three recorded intervals old.")
                st.text(display(heartbeat["component"]))
    _section(st, view.sections[page])
    if page == "Overview":
        try:
            reports = load_backtests(root, source=source)
        except (OSError, ValueError) as exc:
            st.text("Backtest reports unavailable")
            st.text(display(exc))
        else:
            for report in reports:
                for label in report["labels"]:
                    st.text(display(label))
                st.text(display(f"Command: {report['command']}"))
                _plain(st, report)
    if page == "Operations":
        st.text("Manual halt stops new entries. Exits and protective stops continue.")
        with st.form("manual_halt"):
            actor = st.text_input("Actor")
            reason = st.text_input("Reason")
            confirmation = st.text_input("Type HALT to confirm")
            submitted = st.form_submit_button("Halt new entries")
        if submitted:
            if confirmation != "HALT" or not reason.strip():
                st.text("Type HALT and provide a reason.")
            else:
                try:
                    halt_id = request_manual_halt(
                        path, display(reason), actor, now or datetime.now(UTC)
                    )
                except (ValueError, RuntimeError) as exc:
                    st.text(display(exc))
                else:
                    st.text(f"Manual halt recorded: {halt_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("cache", "synthetic"), default="cache")
    args = parser.parse_args()
    render(importlib.import_module("streamlit"), source=args.source)


if __name__ == "__main__":
    main()
