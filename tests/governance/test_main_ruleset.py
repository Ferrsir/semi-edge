"""The main-branch ruleset in docs/governance/main-ruleset.json stays fail closed (#4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.governance]
ROOT = Path(__file__).resolve().parents[2]
RULESET = ROOT / "docs/governance/main-ruleset.json"
GITHUB_ACTIONS_APP_ID = 15368  # check-run app id of the github-actions app on this repository


def _ruleset() -> dict[str, Any]:
    data = json.loads(RULESET.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _rule(rules: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = [r for r in rules if r.get("type") == kind]
    assert len(matches) == 1, f"expected exactly one {kind} rule, found {len(matches)}"
    return matches[0]


def _workflow_check_names() -> set[str]:
    """Check-run names the workflows can publish: a job's `name:` or, without one, its key."""
    names: set[str] = set()
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for key, job in (doc.get("jobs") or {}).items():
            names.add(str(job.get("name", key)) if isinstance(job, dict) else key)
    return names


def test_ruleset_targets_main_with_no_bypass() -> None:
    data = _ruleset()
    assert data["target"] == "branch"
    assert data["enforcement"] == "active"
    assert data["bypass_actors"] == []
    assert data["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert data["conditions"]["ref_name"]["exclude"] == []


def test_ruleset_blocks_deletion_force_push_and_direct_push() -> None:
    rules = _ruleset()["rules"]
    _rule(rules, "deletion")
    _rule(rules, "non_fast_forward")
    pr = _rule(rules, "pull_request")["parameters"]
    assert pr["require_code_owner_review"] is True
    assert pr["dismiss_stale_reviews_on_push"] is True
    assert pr["allowed_merge_methods"] == ["squash"]


def test_required_checks_are_the_four_aggregates_bound_to_actions() -> None:
    checks = _rule(_ruleset()["rules"], "required_status_checks")["parameters"]
    assert checks["strict_required_status_checks_policy"] is True
    contexts = checks["required_status_checks"]
    assert {c["context"] for c in contexts} == {
        "ci-required",
        "integration-required",
        "security-required",
        "trading-safety-required",
    }
    assert all(c.get("integration_id") == GITHUB_ACTIONS_APP_ID for c in contexts)


def test_every_required_check_is_published_by_a_workflow() -> None:
    """A renamed aggregate job would leave a required check that never reports."""
    checks = _rule(_ruleset()["rules"], "required_status_checks")["parameters"]
    missing = {c["context"] for c in checks["required_status_checks"]} - _workflow_check_names()
    assert missing == set()
