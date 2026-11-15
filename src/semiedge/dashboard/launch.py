"""Loopback-only optional UI launcher, without loading runtime settings."""

import importlib.util
import subprocess
import sys
from pathlib import Path


def launch(source: str) -> int:
    if source not in {"cache", "synthetic", "sim"}:
        raise ValueError("source must be cache, synthetic or sim")
    if importlib.util.find_spec("streamlit") is None:
        raise ValueError("Dashboard requires: uv sync --locked --extra dashboard")
    app = Path(__file__).with_name("app.py")
    return subprocess.call(  # noqa: S603 # fixed interpreter/module, validated source
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app),
            "--server.address=127.0.0.1",
            "--browser.gatherUsageStats=false",
            "--",
            "--source",
            source,
        ]
    )
