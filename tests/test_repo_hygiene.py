"""Regression: every source/test/config file must be tracked by git, never silently ignored.

An unanchored ``data/`` rule in .gitignore once excluded the whole ``src/semiedge/data``
package, so a fresh clone could not import ``semiedge.data.schema`` (AUDIT_REPORT F-001).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARDED_DIRS = ("src", "tests", "config", "scripts", "project-status")


def _git_available() -> bool:
    return shutil.which("git") is not None and (ROOT / ".git").exists()


@pytest.mark.skipif(not _git_available(), reason="requires a git checkout")
def test_no_source_file_is_gitignored() -> None:
    candidates = [
        str(p.relative_to(ROOT).as_posix())
        for d in GUARDED_DIRS
        if (ROOT / d).is_dir()
        for p in (ROOT / d).rglob("*")
        if p.is_file()
        and p.suffix
        in {".py", ".yaml", ".yml", ".toml", ".json", ".md", ".js", ".cjs", ".html", ".css"}
        and not {"__pycache__", "node_modules", ".venv"}.intersection(p.parts)
    ]
    assert candidates, "expected source files to check"
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        input="\n".join(candidates),
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    ignored = [line for line in result.stdout.splitlines() if line.strip()]
    assert ignored == [], f"source files are excluded by .gitignore: {ignored}"


def test_data_package_importable() -> None:
    from semiedge.data import schema

    assert schema.validate_bar_frame is not None
    assert {f.value for f in schema.DataFeed} >= {"iex", "sip", "synthetic"}
