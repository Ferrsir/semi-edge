"""Refresh allowlisted project metadata using local GitHub CLI authentication."""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PHASES = {
    "T0": 0,
    "A37": 0,
    "A40": 0,
    "T3b": 0,
    "T47": 0,
    "T1": 1,
    "T2": 1,
    "T3": 1,
    "T4": 1,
    "T42": 1,
    "T5": 2,
    "T6": 2,
    "T7": 2,
    "T17": 2,
    "T46": 2,
    "T8": 3,
    "T9": 3,
    "T10": 3,
    "T11": 3,
    "T12": 3,
    "T13": 4,
    "T14": 4,
    "T15": 4,
    "T16": 4,
    "H1": 3,
    "H2": 3,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    gh = shutil.which("gh") or "C:/Program Files/GitHub CLI/gh.exe"

    def github(*command):
        return json.loads(
            subprocess.check_output(  # noqa: S603 -- fixed gh argument arrays, no shell
                [gh, *command], cwd=args.repo, text=True, encoding="utf-8"
            )
        )

    issues = github(
        "issue", "list", "--state", "all", "--limit", "100", "--json", "number,title,state,url"
    )
    prs = github(
        "pr",
        "list",
        "--state",
        "all",
        "--limit",
        "30",
        "--json",
        "number,title,state,url,headRefName,headRefOid,statusCheckRollup",
    )
    tasks = []
    for line in (args.repo / "AGENTS.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) != 6 or cells[0] not in PHASES:
            continue
        task_id, issue, title, owner, raw, dependencies = cells
        status = next(
            (s for s in ["DONE", "IN PROGRESS", "REVIEW", "BLOCKED", "READY"] if raw.startswith(s)),
            "READY",
        )
        tasks.append(
            {
                "id": task_id,
                "issue": issue,
                "title": title,
                "owner": owner,
                "status": status,
                "raw": raw,
                "dependencies": dependencies,
                "phase": PHASES[task_id],
            }
        )
    for number, title in [
        (28, "AI research adapters"),
        (30, "Paper results vs backtest expectations"),
        (31, "Live-trading readiness research only"),
    ]:
        tasks.append(
            {
                "id": f"R{number}",
                "issue": f"#{number}",
                "title": title,
                "owner": "unassigned",
                "status": "READY",
                "raw": "Backlog; research only, no live execution authorized",
                "dependencies": "Phase 4",
                "phase": 5,
            }
        )
    result = {
        "schema_version": 1,
        "checked_at": datetime.now(UTC).isoformat(),
        "repo": "Ferrsir/semi-edge",
        "source": "AGENTS.md + GitHub issues and PRs",
        "artifact_accessible": False,
        "tasks": tasks,
        "issues": issues,
        "prs": prs,
    }
    destination = Path(__file__).resolve().parents[1] / "dist" / "snapshot.js"
    destination.write_text(
        "window.PROJECT_SNAPSHOT = "
        + json.dumps(result, ensure_ascii=True).replace("<", "\\u003c")
        + ";\n",
        encoding="utf-8",
    )
    sys.stdout.write(f"Refreshed {len(tasks)} tasks, {len(prs)} PRs; no credentials exported\n")


if __name__ == "__main__":
    main()
