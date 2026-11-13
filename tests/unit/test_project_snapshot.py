from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "project-status/scripts/refresh-snapshot.py"
BOARD = """## Board
| ID | Issue | Task | Owner | Status | Depends on |
|---|---|---|---|---|---|
| T1 | #9 | Bars | codex | REVIEW (PR #36) | T0 |
## Review queue
| PR | Task | Author | Reviewer | Verdict |
|---|---|---|---|---|
| #36 | T1 | codex | claude | FAIL at abc1234; fix needed |
## Discussion log
| T50 | #50 | not a board row | codex | ignored here | T1 |
"""
ISSUES = [{"number": n, "title": f"Roadmap {n}", "state": "OPEN"} for n in [28, 30, 31]]


@pytest.fixture
def snapshot_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("project_snapshot", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preserves_board_review_and_labels_roadmap(snapshot_module: ModuleType) -> None:
    result = snapshot_module.build_snapshot(BOARD, ISSUES, [], datetime(2026, 1, 1, tzinfo=UTC))
    assert result["tasks"][0]["status"] == "REVIEW"
    assert len(result["tasks"]) == 4
    assert result["review_queue"][0]["verdict"] == "FAIL at abc1234; fix needed"
    assert result["tasks"][-1]["source"] == "roadmap_placeholder"


@pytest.mark.parametrize(
    "board",
    [
        BOARD.replace("| T1 |", "| T50 |", 1),
        BOARD.replace("REVIEW (PR #36)", "Merged via PR #60"),
        BOARD.replace("REVIEW (PR #36)", "Done"),
        BOARD.replace("| T1 | #9 | Bars", "| T1 | #9"),
        BOARD.replace("## Board", "## Other"),
        BOARD.replace("## Review queue", "## Other"),
    ],
)
def test_board_drift_fails_closed(snapshot_module: ModuleType, board: str) -> None:
    with pytest.raises(snapshot_module.SnapshotError):
        snapshot_module.build_snapshot(board, ISSUES, [], datetime(2026, 1, 1, tzinfo=UTC))


def test_missing_github_cli_fails_explicitly(
    snapshot_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(snapshot_module.shutil, "which", lambda _: None)
    destination = tmp_path / "snapshot.js"
    destination.write_text("previous snapshot", encoding="utf-8")
    with pytest.raises(snapshot_module.SnapshotError, match="PATH"):
        snapshot_module.refresh_snapshot(tmp_path, destination)
    assert destination.read_text() == "previous snapshot"


@pytest.mark.parametrize("payload", [[{}] * 1000, {"items": []}, [None]])
def test_truncated_or_invalid_github_data_fails(
    snapshot_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: object
) -> None:
    monkeypatch.setattr(
        snapshot_module.subprocess, "check_output", lambda *a, **kw: json.dumps(payload)
    )
    with pytest.raises(snapshot_module.SnapshotError):
        snapshot_module.read_github("gh", tmp_path, "pr", "list")


def test_failed_refresh_preserves_previous_snapshot(
    snapshot_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "AGENTS.md").write_text(
        BOARD.replace("REVIEW (PR #36)", "unknown"), encoding="utf-8"
    )
    destination = tmp_path / "snapshot.js"
    destination.write_text("previous snapshot", encoding="utf-8")
    monkeypatch.setattr(snapshot_module.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(snapshot_module, "read_github", lambda *args: ISSUES)
    with pytest.raises(snapshot_module.SnapshotError):
        snapshot_module.refresh_snapshot(tmp_path, destination)
    assert destination.read_text() == "previous snapshot"


def test_successful_refresh_replaces_snapshot(
    snapshot_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "AGENTS.md").write_text(BOARD, encoding="utf-8")
    destination = tmp_path / "snapshot.js"
    monkeypatch.setattr(snapshot_module.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(
        snapshot_module, "read_github", lambda *args: ISSUES if args[2] == "issue" else []
    )
    result = snapshot_module.refresh_snapshot(tmp_path, destination)
    assert result["schema_version"] == 2
    assert destination.read_text().startswith("window.PROJECT_SNAPSHOT = ")
    assert not list(tmp_path.glob("*.tmp"))


def test_closed_roadmap_issue_reflects_github(snapshot_module: ModuleType) -> None:
    issues = [{**i, "state": "CLOSED"} for i in ISSUES]
    result = snapshot_module.build_snapshot(BOARD, issues, [], datetime(2026, 1, 1, tzinfo=UTC))
    assert all(task["status"] == "DONE" for task in result["tasks"][1:])
