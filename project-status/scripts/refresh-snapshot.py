"""Refresh allowlisted project metadata using local GitHub CLI authentication."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

PHASES = {
    "T0": 0,
    "A37": 0,
    "A40": 0,
    "T3b": 0,
    "T47": 0,
    "H3": 0,
    "T1": 1,
    "T2": 1,
    "T3": 1,
    "T4": 1,
    "T42": 1,
    "A63": 1,
    "T5": 2,
    "T6": 2,
    "T7": 2,
    "T17": 2,
    "T46": 2,
    "A68": 2,
    "T71": 2,
    "T8": 3,
    "T9": 3,
    "T10": 3,
    "T11": 3,
    "T12": 3,
    "T13": 4,
    "T14": 4,
    "T15": 4,
    "T16": 4,
    "A74": 4,
    "H1": 3,
    "H2": 3,
    "A79": 2,
    "A98": 4,
    "A103": 4,
    "A122": 4,
    "I128": 3,
    "I131": 2,
    "I133": 2,
    "I137": 2,
    "I141": 2,
    "I149": 2,
    "A146": 2,
    "A154": 2,
    "T155": 4,
}


MAX_ITEMS = 1000
JsonRows = list[dict[str, Any]]


class SnapshotError(ValueError):
    """Source data cannot be represented faithfully; keep the previous snapshot."""


def table_rows(board: str, section: str, width: int) -> list[list[str]]:
    heading = f"## {section}"
    lines = board.splitlines()
    if heading not in lines:
        raise SnapshotError(f"Missing {section} section")
    rows = []
    for line in lines[lines.index(heading) + 1 :]:
        if line.startswith("## "):
            break
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if cells and cells[0] in {"ID", "PR"}:
            continue
        if cells and all(re.fullmatch(r"[-:]+", cell) for cell in cells):
            continue
        if len(cells) != width:
            raise SnapshotError(f"Malformed row in {section}")
        rows.append(cells)
    return rows


def build_snapshot(
    board: str, issues: JsonRows, prs: JsonRows, checked_at: datetime
) -> dict[str, Any]:
    tasks: JsonRows = []
    seen: set[str] = set()
    for cells in table_rows(board, "Board", 6):
        task_id, issue, title, owner, raw, dependencies = cells
        if task_id not in PHASES:
            raise SnapshotError(
                f"Unknown task ID {task_id}: update the explicit phase mapping before refresh"
            )
        if task_id in seen:
            raise SnapshotError("Duplicate task ID in Board")
        seen.add(task_id)
        match = re.fullmatch(r"(DONE|IN PROGRESS|REVIEW|BLOCKED|READY)(?:\s.*)?", raw)
        if match is None:
            raise SnapshotError(f"Unsupported status for {task_id}; update Board before refresh")
        tasks.append(
            {
                "id": task_id,
                "issue": issue,
                "title": title,
                "owner": owner,
                "status": match[1],
                "raw": raw,
                "dependencies": dependencies,
                "phase": PHASES[task_id],
                "source": "coordination_board",
            }
        )
    if not tasks:
        raise SnapshotError("Board contains no task rows")
    review_queue = []
    review_ids: set[int] = set()
    for pr, task, author, reviewer, verdict in table_rows(board, "Review queue", 5):
        if re.fullmatch(r"#[1-9][0-9]*", pr) is None:
            raise SnapshotError("Invalid PR identifier in Review queue")
        number = int(pr[1:])
        if number in review_ids:
            raise SnapshotError("Duplicate PR in Review queue")
        review_ids.add(number)
        review_queue.append(
            {
                "number": number,
                "task": task,
                "author": author,
                "reviewer": reviewer,
                "verdict": verdict,
            }
        )
    for number in [28, 30, 31]:
        issue_data = next((i for i in issues if i["number"] == number), None)
        if issue_data is None:
            raise SnapshotError(f"Roadmap issue #{number} missing from GitHub response")
        if issue_data.get("state") not in {"OPEN", "CLOSED"}:
            raise SnapshotError(f"Roadmap issue #{number} has an unsupported state")
        tasks.append(
            {
                "id": f"R{number}",
                "issue": f"#{number}",
                "title": issue_data["title"],
                "owner": "unassigned",
                "status": "DONE" if issue_data["state"] == "CLOSED" else "READY",
                "raw": (
                    f"Roadmap placeholder; GitHub issue {issue_data['state']}; "
                    "not assigned on Board"
                ),
                "dependencies": "Phase 4",
                "phase": 5,
                "source": "roadmap_placeholder",
            }
        )
    return {
        "schema_version": 2,
        "checked_at": checked_at.isoformat(),
        "repo": "Ferrsir/semi-edge",
        "source": "BOARD.md Board + Review queue; GitHub issues and PRs",
        "artifact_accessible": False,
        "tasks": tasks,
        "issues": issues,
        "prs": prs,
        "review_queue": review_queue,
    }


def read_github(gh: str, repo: Path, *command: str) -> JsonRows:
    raw: object = json.loads(
        subprocess.check_output(  # noqa: S603 -- fixed argv, no shell
            [gh, *command], cwd=repo, text=True, encoding="utf-8"
        )
    )
    if not isinstance(raw, list) or not all(isinstance(row, dict) for row in raw):
        raise SnapshotError("GitHub returned invalid metadata")
    if len(raw) >= MAX_ITEMS:
        raise SnapshotError("GitHub metadata reached its item limit; add pagination before refresh")
    return cast(JsonRows, raw)


def refresh_snapshot(repo: Path, destination: Path) -> dict[str, Any]:
    gh = shutil.which("gh")
    if gh is None:
        raise SnapshotError("GitHub CLI gh must be installed and on PATH")
    board_path = repo / "BOARD.md"
    if not board_path.is_file():
        raise SnapshotError(f"board file {board_path} is missing; the board lives in BOARD.md")
    board = board_path.read_text(encoding="utf-8")
    issues = read_github(
        gh,
        repo,
        "issue",
        "list",
        "--state",
        "all",
        "--limit",
        str(MAX_ITEMS),
        "--json",
        "number,title,state,url",
    )
    prs = read_github(
        gh,
        repo,
        "pr",
        "list",
        "--state",
        "all",
        "--limit",
        str(MAX_ITEMS),
        "--json",
        "number,title,state,url,headRefName,headRefOid,statusCheckRollup",
    )
    result = build_snapshot(board, issues, prs, datetime.now(UTC))
    payload = (
        "window.PROJECT_SNAPSHOT = "
        + json.dumps(result, ensure_ascii=True).replace("<", "\\u003c")
        + ";\n"
    )
    descriptor, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args(argv)
    destination = Path(__file__).resolve().parents[1] / "dist" / "snapshot.js"
    try:
        result = refresh_snapshot(args.repo, destination)
    except SnapshotError as exc:
        sys.stderr.write(f"Snapshot unchanged: {exc}\n")
        return 2
    except (OSError, ValueError, subprocess.SubprocessError):
        sys.stderr.write("Snapshot unchanged: unable to read source metadata or write output\n")
        return 2
    sys.stdout.write(f"Refreshed {len(result['tasks'])} tasks and {len(result['prs'])} PRs\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
