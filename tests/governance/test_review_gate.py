from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from review_common import load_policy
from review_gate import (
    BOT_LOGIN,
    Approval,
    Comment,
    GitHub,
    NoRedirect,
    count_failed_cycles,
    decide,
    load_reports,
    render_comment,
)

pytestmark = pytest.mark.governance

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / ".github/review/policy.json")
HEAD = "a" * 40
OLD = "b" * 40


def output(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "verdict": "PASS",
        "summary": "ok",
        "files_inspected": ["src/x.py"],
        "tests_inspected": ["tests/test_x.py"],
        "findings": [],
        "required_changes": [],
    }
    base.update(overrides)
    return base


def finding(sev: str, tracked: str | None = None) -> dict[str, Any]:
    return {
        "id": "F1",
        "severity": sev,
        "title": f"{sev} issue",
        "file": "src/x.py",
        "line": 3,
        "description": "d",
        "suggested_fix": "f",
        "tracked_issue": tracked,
    }


def report(role: str, *, head: str = HEAD, **kw: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "role": role,
        "head_sha": head,
        "repository": "o/r",
        "pr_number": 33,
        "base_sha": OLD,
        "execution_id": "00000000-0000-4000-8000-" + ("1" if role == "A" else "2") * 12,
        "reviewer": {"provider": "openai-codex" if role == "A" else "anthropic-claude"},
        "model_output": output(**kw),
        "error": None,
    }


def passing() -> dict[str, dict[str, Any] | None]:
    return {"A": report("A"), "B": report("B")}


def run(reports: dict[str, Any] | None = None, **kw: Any) -> Any:
    params: dict[str, Any] = {
        "head_sha": HEAD,
        "repository": "o/r",
        "pr_number": 33,
        "base_sha": OLD,
        "reports": passing() if reports is None else reports,
        "changed_files": ["src/semiedge/strategy/momentum_v1.py"],
        "approvals": [],
        "failed_cycles": 0,
    }
    params.update(kw)
    return decide(POLICY, **params)


def test_two_passing_reviews_of_current_head_pass() -> None:
    d = run()
    assert d.state == "success"
    assert not d.reasons
    assert not d.cycle_failed


@pytest.mark.parametrize("missing", ["A", "B"])
def test_missing_review_fails(missing: str) -> None:
    reports = passing()
    reports[missing] = None
    d = run(reports)
    assert d.state == "failure"
    assert any(f"Review {missing} missing" in r for r in d.reasons)


def test_stale_review_of_older_commit_fails() -> None:
    reports = passing()
    reports["B"] = report("B", head=OLD)
    d = run(reports)
    assert d.state == "failure"
    assert any("stale" in r for r in d.reasons)


@pytest.mark.parametrize("sev", ["P0", "P1"])
def test_blocking_findings_fail_even_if_model_says_pass(sev: str) -> None:
    reports = passing()
    reports["A"] = report("A", findings=[finding(sev)], verdict="PASS")
    assert run(reports).state == "failure"


def test_p2_requires_open_tracked_issue() -> None:
    reports = passing()
    reports["A"] = report("A", findings=[finding("P2")])
    assert run(reports).state == "failure"
    reports["A"] = report("A", findings=[finding("P2", "#41")])
    assert run(reports, open_issue_refs={"#41"}).state == "success"
    assert run(reports, open_issue_refs=set()).state == "failure"  # closed/nonexistent issue


def test_p3_does_not_block() -> None:
    reports = passing()
    reports["B"] = report("B", findings=[finding("P3")])
    assert run(reports).state == "success"


def test_model_fail_verdict_is_respected() -> None:
    reports = passing()
    reports["B"] = report("B", verdict="FAIL")
    assert run(reports).state == "failure"


def test_review_error_fails() -> None:
    reports = passing()
    reports["A"] = {"role": "A", "head_sha": HEAD, "model_output": None, "error": "secret missing"}
    d = run(reports)
    assert d.state == "failure"
    assert any("secret missing" in r for r in d.reasons)


def test_reviewer_that_inspected_nothing_fails() -> None:
    reports = passing()
    reports["A"] = report("A", files_inspected=[])
    assert run(reports).state == "failure"


def test_malformed_output_fails() -> None:
    reports = passing()
    bad = report("A")
    bad["model_output"]["findings"] = [{"severity": "P9"}]
    reports["A"] = bad
    assert run(reports).state == "failure"


def test_role_mismatch_fails() -> None:
    reports = passing()
    reports["A"] = report("B")
    assert run(reports).state == "failure"


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        "AGENTS.md",
        "CLAUDE.md",
        "docs/governance/ENGINEERING_POLICY.md",
        "config/risk.yaml",
        "src/semiedge/safety/broker.py",
        "scripts/governance/review_gate.py",
        ".claude/settings.json",
        ".codex/config.toml",
    ],
)
def test_restricted_paths_require_human_approval_of_exact_head(path: str) -> None:
    files = ["src/semiedge/cli.py", path]
    d = run(changed_files=files)
    assert d.state == "failure"
    assert path in d.restricted_files
    assert not d.cycle_failed  # waiting for a human does not consume repair cycles
    stale = [Approval("Ferrsir", "APPROVED", OLD)]
    assert run(changed_files=files, approvals=stale).state == "failure"
    by_agent = [Approval("some-bot", "APPROVED", HEAD)]
    assert run(changed_files=files, approvals=by_agent).state == "failure"
    commented = [Approval("Ferrsir", "COMMENTED", HEAD)]
    assert run(changed_files=files, approvals=commented).state == "failure"
    ok = [Approval("ferrsir", "APPROVED", HEAD)]
    assert run(changed_files=files, approvals=ok).state == "success"


def test_unrestricted_lookalike_paths_are_not_restricted() -> None:
    d = run(
        changed_files=[
            "docs/governance-notes.md",
            "src/semiedge/safety_utils.py",
            "config/risk.yml.bak",
        ]
    )
    assert d.restricted_files == []


def test_escalates_after_max_cycles() -> None:
    reports = passing()
    reports["A"] = report("A", findings=[finding("P1")])
    assert not run(reports, failed_cycles=1).escalate
    d = run(reports, failed_cycles=2)
    assert d.escalate
    assert d.reasons[0].startswith("escalated")


def test_escalation_blocks_even_a_passing_review() -> None:
    d = run(failed_cycles=3)
    assert d.state == "failure"
    assert d.escalate


def test_draft_is_not_evaluated_and_does_not_consume_cycles() -> None:
    d = run(is_draft=True)
    assert d.state == "failure"
    assert not d.cycle_failed


def _gate_comment(head: str, state: str, cycle: str, at: str, login: str = BOT_LOGIN) -> Comment:
    body = f"<!-- semi-edge-review-gate v1 head={head} state={state} cycle={cycle} -->\nbody"
    return Comment(login, body, at)


def test_count_failed_cycles_distinct_heads_bot_only_and_resets() -> None:
    c1, c2, c3 = "1" * 40, "2" * 40, "3" * 40
    comments = [
        _gate_comment(c1, "failure", "failed", "2026-01-01T00:00:00Z"),
        _gate_comment(c1, "failure", "failed", "2026-01-01T00:05:00Z"),  # same head counted once
        _gate_comment(c2, "failure", "ok", "2026-01-02T00:00:00Z"),  # restricted wait only
        _gate_comment(c3, "failure", "failed", "2026-01-03T00:00:00Z", login="Ferrsir"),  # spoof
        _gate_comment(HEAD, "failure", "failed", "2026-01-04T00:00:00Z"),  # current head excluded
    ]
    kw = {
        "current_head": HEAD,
        "humans": ["Ferrsir"],
        "reset_command": "/semi-edge reset-review-cycles",
    }
    assert count_failed_cycles(comments, **kw) == 1
    comments.append(_gate_comment(c3, "failure", "failed", "2026-01-05T00:00:00Z"))
    assert count_failed_cycles(comments, **kw) == 2
    # reset by a non-maintainer is ignored; by a maintainer it clears earlier failures
    comments.append(Comment("some-bot", "/semi-edge reset-review-cycles", "2026-01-06T00:00:00Z"))
    assert count_failed_cycles(comments, **kw) == 2
    comments.append(Comment("Ferrsir", "/semi-edge reset-review-cycles", "2026-01-07T00:00:00Z"))
    assert count_failed_cycles(comments, **kw) == 0


def test_render_comment_marker_roundtrip() -> None:
    d = run()
    body = render_comment(
        d, head_sha=HEAD, reports=passing(), run_url=None, render_report=lambda r: "R"
    )
    parsed = count_failed_cycles(
        [Comment(BOT_LOGIN, body.replace("cycle=ok", "cycle=failed"), "2026-01-01T00:00:00Z")],
        current_head=OLD,
        humans=[],
        reset_command="/x",
    )
    assert parsed == 1
    assert f"head={HEAD}" in body


def test_load_reports_invalid_json_is_ignored(tmp_path: Path) -> None:
    import json

    (tmp_path / "review-a.json").write_text(json.dumps(report("A")))
    (tmp_path / "review-b.json").write_text("{not json")
    loaded = load_reports(tmp_path)
    assert set(loaded) == {"A"}


def test_policy_requires_two_roles(tmp_path: Path) -> None:
    import json

    from review_common import PolicyError

    bad = copy.deepcopy(POLICY)
    bad["required_reviews"] = bad["required_reviews"][:1]
    path = tmp_path / "p.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(PolicyError, match="two distinct"):
        load_policy(path)
    bad = copy.deepcopy(POLICY)
    bad["blocking_severities"] = ["P0"]
    path.write_text(json.dumps(bad))
    with pytest.raises(PolicyError, match="P1"):
        load_policy(path)


@pytest.mark.parametrize("state", ["CHANGES_REQUESTED", "DISMISSED"])
def test_latest_human_opinion_revokes_approval(state: str) -> None:
    approvals = [Approval("Ferrsir", "APPROVED", HEAD), Approval("ferrsir", state, HEAD)]
    assert run(changed_files=["AGENTS.md"], approvals=approvals).state == "failure"


def test_comment_does_not_revoke_human_approval() -> None:
    approvals = [
        Approval("Ferrsir", "APPROVED", HEAD),
        Approval("Ferrsir", "COMMENTED", HEAD),
    ]
    assert run(changed_files=["AGENTS.md"], approvals=approvals).state == "success"


def test_required_changes_block_even_with_pass_verdict() -> None:
    reports = passing()
    reports["A"] = report("A", required_changes=["Fix the API contract"])
    assert run(reports).state == "failure"


@pytest.mark.parametrize("payload", ["null", "[]", '"text"', "123"])
def test_non_object_reports_are_ignored(tmp_path: Path, payload: str) -> None:
    (tmp_path / "review-a.json").write_text(payload)
    assert load_reports(tmp_path) == {}


def test_duplicate_role_reports_fail_closed(tmp_path: Path) -> None:
    import json

    for suffix in ("a", "b", "c"):
        (tmp_path / f"review-{suffix}.json").write_text(json.dumps(report("A")))
    assert load_reports(tmp_path) == {"A": None}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("repository", "other/repo"),
        ("pr_number", 34),
        ("base_sha", HEAD),
        ("reviewer", {}),
        ("execution_id", None),
        ("execution_id", "not-a-uuid"),
    ],
)
def test_report_metadata_mismatch_fails(key: str, value: Any) -> None:
    reports = passing()
    bad = report("A")
    bad[key] = value
    reports["A"] = bad
    assert run(reports).state == "failure"


def test_duplicate_execution_ids_fail() -> None:
    a, b = report("A"), report("B")
    b["execution_id"] = a["execution_id"]
    assert run({"A": a, "B": b}).state == "failure"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com.attacker.test/repos/o/r",
        "http://api.github.com/repos/o/r",
        "https://api.github.com:444/repos/o/r",
        "https://user@api.github.com/repos/o/r",
    ],
)
def test_github_rejects_unexpected_origin(url: str) -> None:
    with pytest.raises(ValueError, match="origin"):
        GitHub("o/r", "example")._request("GET", url)


def test_github_rejects_redirects() -> None:
    with pytest.raises(ValueError, match="redirects"):
        NoRedirect().redirect_request(None, None, 302, "", None, "https://attacker.test")


# ------------------------------------------------- infrastructure errors vs review cycles (#54)


def infra_report(role: str, *, head: str = HEAD) -> dict[str, Any]:
    rep = report(role, head=head)
    rep["model_output"] = None
    rep["error"] = "reviewer not configured: repository secret OPENAI_API_KEY is missing"
    rep["error_kind"] = "infrastructure"
    return rep


def _simulate_runs(make_reports: Any, runs: int = 3) -> list[Any]:
    """Run the gate on ``runs`` successive head commits, feeding back its own comments."""
    comments: list[Comment] = []
    decisions = []
    for i in range(runs):
        head = str(i + 1) * 40
        failed = count_failed_cycles(
            comments,
            current_head=head,
            humans=POLICY["human_maintainers"],
            reset_command="/semi-edge reset-review-cycles",
        )
        reports = make_reports(head)
        d = decide(
            POLICY,
            head_sha=head,
            repository="o/r",
            pr_number=33,
            base_sha=OLD,
            reports=reports,
            changed_files=["src/semiedge/cli.py"],
            approvals=[],
            failed_cycles=failed,
        )
        body = render_comment(
            d, head_sha=head, reports=reports, run_url=None, render_report=lambda r: "R"
        )
        comments.append(Comment(BOT_LOGIN, body, f"2026-01-0{i + 1}T00:00:00Z"))
        decisions.append(d)
    return decisions


def test_three_runs_without_secrets_fail_but_do_not_escalate() -> None:
    decisions = _simulate_runs(
        lambda h: {"A": infra_report("A", head=h), "B": infra_report("B", head=h)}
    )
    for d in decisions:
        assert d.state == "failure"  # still fail closed
        assert not d.cycle_failed
        assert not d.escalate
        assert d.failed_cycles == 0
        assert d.infrastructure_roles == ["A", "B"]
        assert any("infrastructure error" in r for r in d.reasons)


def test_three_real_fail_reviews_still_escalate() -> None:
    def failing(h: str) -> dict[str, Any]:
        return {"A": report("A", head=h, findings=[finding("P1")]), "B": report("B", head=h)}

    decisions = _simulate_runs(failing)
    assert [d.failed_cycles for d in decisions] == [1, 2, 3]
    assert [d.escalate for d in decisions] == [False, False, True]
    assert all(d.cycle_failed for d in decisions)


def test_real_failure_alongside_infrastructure_error_counts() -> None:
    d = run({"A": report("A", verdict="FAIL"), "B": infra_report("B")})
    assert d.state == "failure"
    assert d.cycle_failed
    assert d.infrastructure_roles == ["B"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(error_kind="review"),  # a real reviewer error
        lambda r: r.pop("error_kind"),  # unmarked error (legacy report)
        lambda r: r.update(model_output={"verdict": "FAIL"}),  # reviewer ran
        lambda r: r.update(error=""),  # marker without an error
        lambda r: r.update(head_sha=OLD),  # stale report
    ],
)
def test_only_well_formed_infrastructure_reports_skip_cycles(mutate: Any) -> None:
    rep = infra_report("A")
    mutate(rep)
    d = run({"A": rep, "B": report("B")})
    assert d.state == "failure"
    assert d.cycle_failed
    assert d.infrastructure_roles == []


def test_missing_report_still_counts_as_a_cycle() -> None:
    d = run({"A": None, "B": report("B")})
    assert d.state == "failure"
    assert d.cycle_failed
