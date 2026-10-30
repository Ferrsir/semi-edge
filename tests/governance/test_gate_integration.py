import argparse
import json
from pathlib import Path
from typing import Any

import pytest
import review_gate

pytestmark = [pytest.mark.integration, pytest.mark.governance]


@pytest.mark.parametrize(
    "output",
    [
        {"findings": None},
        "bad",
        {"findings": [None]},
        {
            "verdict": "PASS",
            "summary": "ok",
            "files_inspected": ["a.py"],
            "tests_inspected": [],
            "findings": [],
            "required_changes": [],
        },
    ],
)
def test_malformed_nested_report_publishes_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: Any
) -> None:
    head = "a" * 40
    states: list[str] = []

    class FakeGitHub:
        def __init__(self, *args: Any) -> None:
            pass

        def pr(self, number: int) -> dict[str, Any]:
            return {"head": {"sha": head}, "base": {"sha": "b" * 40}}

        def __getattr__(self, name: str) -> Any:
            if name == "set_status":
                return lambda sha, state, *args: states.append(state)
            return lambda *args: []

    (tmp_path / "review-a.json").write_text(
        json.dumps({"role": "A", "model_output": output, "reviewer": {}, "verdict_reasons": 1}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(review_gate, "GitHub", FakeGitHub)
    args = argparse.Namespace(
        trusted_root=Path(__file__).resolve().parents[2],
        repository="o/r",
        pr=33,
        head=head,
        reports_dir=tmp_path,
        run_url=None,
        dry_run=False,
    )
    assert review_gate.run_github(args) == 0
    assert states == ["failure"]
