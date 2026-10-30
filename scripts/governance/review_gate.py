"""SEMI-EDGE AI review gate: decide whether a PR head may merge, and publish the result.

RESTRICTED PATH (scripts/governance/**): changes require explicit human approval.

Runs inside the trusted ``ai-review`` workflow (definition from the base branch). It publishes
the commit status ``semi-edge/ai-review-gate`` on the PR head SHA, which the main-branch
ruleset requires (bound to the GitHub Actions app). The gate passes only when:

1. every required review role produced a report for EXACTLY the current head SHA;
2. every report's effective verdict, recomputed here from its findings, is PASS
   (no P0/P1; P2 only when deferred to an existing open issue);
3. restricted paths are untouched, or a human maintainer APPROVED this exact head SHA;
4. the PR has not exhausted ``max_review_cycles`` failed review cycles since the last
   human reset (otherwise it is escalated with the ``needs-human`` label).

Usage (CI):
  python scripts/governance/review_gate.py github --reports-dir reports --repository OWNER/REPO \
      --pr 33 --head <sha> [--run-url URL] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, override
from urllib.parse import urlsplit
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_review import render_markdown
from review_common import (
    effective_verdict,
    load_policy,
    redact,
    restricted_matches,
)

TRUSTED_ROOT_DEFAULT = Path(__file__).resolve().parents[2]
GATE_MARKER = "semi-edge-review-gate v1"
BOT_LOGIN = "github-actions[bot]"
_MARKER_RE = re.compile(
    r"<!-- semi-edge-review-gate v1 head=([0-9a-f]{40}) "
    r"state=(success|failure) cycle=(failed|ok) -->"
)


@dataclass(frozen=True)
class Approval:
    login: str
    state: str
    commit_id: str


@dataclass(frozen=True)
class Comment:
    login: str
    body: str
    created_at: str  # ISO-8601; lexicographic order == chronological order


@dataclass
class GateDecision:
    state: str  # "success" | "failure"
    description: str
    escalate: bool = False
    restricted_files: list[str] = field(default_factory=list)
    role_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    failed_cycles: int = 0
    cycle_failed: bool = (
        False  # True when the AI reviews themselves failed (consumes a repair cycle)
    )


# --------------------------------------------------------------------------- pure logic


def count_failed_cycles(
    comments: Sequence[Comment], *, current_head: str, humans: Iterable[str], reset_command: str
) -> int:
    """Distinct head SHAs (other than ``current_head``) whose AI REVIEWS failed, counting only
    gate comments posted after the most recent human reset command. Waiting for a human
    approval on a restricted path does not consume repair cycles."""
    human_set = {h.lower() for h in humans}
    resets = [
        c.created_at
        for c in comments
        if c.login.lower() in human_set and c.body.strip().startswith(reset_command)
    ]
    since = max(resets) if resets else ""
    failed: set[str] = set()
    for c in comments:
        if c.login != BOT_LOGIN or c.created_at <= since:
            continue
        m = _MARKER_RE.search(c.body)
        if m and m.group(3) == "failed" and m.group(1) != current_head:
            failed.add(m.group(1))
    return len(failed)


def has_current_human_approval(
    approvals: Sequence[Approval], humans: set[str], head_sha: str
) -> bool:
    # GitHub returns reviews in chronological order. Comments do not revoke
    # an opinion, but a later rejection or dismissal does.
    latest: dict[str, Approval] = {}
    for approval in approvals:
        if approval.state in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[approval.login.lower()] = approval
    return any(
        a.state == "APPROVED" and a.commit_id == head_sha and login in humans
        for login, a in latest.items()
    )


def report_metadata_errors(
    report: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    head_sha: str,
    repository: str,
    pr_number: int,
    base_sha: str,
    seen_executions: set[str],
) -> list[str]:
    errors = []
    expected = {
        "schema_version": 1,
        "role": spec["role"],
        "head_sha": head_sha,
        "repository": repository,
        "pr_number": pr_number,
        "base_sha": base_sha,
    }
    for key, value in expected.items():
        if type(report.get(key)) is not type(value) or report.get(key) != value:
            errors.append(f"stale or invalid {key}: expected {value!r}")
    reviewer = report.get("reviewer")
    if not isinstance(reviewer, dict) or reviewer.get("provider") != spec["provider"]:
        errors.append("reviewer provider missing or unexpected")
    execution = report.get("execution_id")
    try:
        normalized = str(UUID(execution)) if isinstance(execution, str) else ""
    except ValueError:
        normalized = ""
    if not normalized:
        errors.append("missing or invalid execution_id")
    elif normalized in seen_executions:
        errors.append("review executions must be distinct")
    else:
        seen_executions.add(normalized)
    return errors


def decide(
    policy: Mapping[str, Any],
    *,
    head_sha: str,
    repository: str,
    pr_number: int,
    base_sha: str,
    reports: Mapping[str, Mapping[str, Any] | None],
    changed_files: Sequence[str],
    approvals: Sequence[Approval],
    failed_cycles: int,
    open_issue_refs: set[str] | None = None,
    is_draft: bool = False,
) -> GateDecision:
    reasons: list[str] = []
    role_results: dict[str, dict[str, Any]] = {}

    if is_draft:
        return GateDecision(
            "failure", "Draft PR: AI reviews are not evaluated for drafts.", cycle_failed=False
        )

    seen_executions: set[str] = set()
    for spec in policy["required_reviews"]:
        role = spec["role"]
        report = reports.get(role)
        if report is None:
            reasons.append(f"Review {role} missing")
            role_results[role] = {"verdict": "MISSING", "reasons": ["no report"]}
            continue
        problems = report_metadata_errors(
            report,
            spec,
            head_sha=head_sha,
            repository=repository,
            pr_number=pr_number,
            base_sha=base_sha,
            seen_executions=seen_executions,
        )
        verdict = effective_verdict(
            report.get("model_output"),
            policy,
            error=report.get("error"),
            valid_issue_refs=open_issue_refs,
        )
        problems.extend(verdict.reasons)
        ok = not problems and verdict.verdict == "PASS"
        role_results[role] = {"verdict": "PASS" if ok else "FAIL", "reasons": problems}
        if not ok:
            reasons.extend(f"Review {role}: {p}" for p in problems)

    cycle_failed = bool(reasons)
    restricted = restricted_matches(changed_files, policy["restricted_paths"])
    humans = {h.lower() for h in policy["human_maintainers"]}
    if restricted:
        approved = has_current_human_approval(approvals, humans, head_sha)
        if not approved:
            reasons.append(
                "restricted paths changed; a human maintainer must APPROVE this exact head commit: "
                + ", ".join(restricted)
            )

    total_failed = failed_cycles + (1 if cycle_failed else 0)
    escalate = total_failed >= policy["max_review_cycles"]
    if escalate:
        reasons.insert(
            0,
            f"escalated: {total_failed} failed review cycle(s) reached the limit of "
            f"{policy['max_review_cycles']}; human attention required",
        )

    if reasons:
        description = reasons[0]
        state = "failure"
    else:
        description = "Reviews A and B passed for this exact commit"
        state = "success"
    return GateDecision(
        state=state,
        description=description[:138],
        escalate=escalate,
        restricted_files=restricted,
        role_results=role_results,
        reasons=reasons,
        failed_cycles=total_failed,
        cycle_failed=cycle_failed,
    )


def render_comment(
    decision: GateDecision,
    *,
    head_sha: str,
    reports: Mapping[str, Mapping[str, Any] | None],
    run_url: str | None,
    render_report: Any,
) -> str:
    icon = "✅" if decision.state == "success" else "❌"
    lines = [
        f"<!-- {GATE_MARKER} head={head_sha} state={decision.state} "
        f"cycle={'failed' if decision.cycle_failed else 'ok'} -->",
        f"## {icon} SEMI-EDGE AI review gate: `{decision.state}`",
        "",
        f"- Head commit: `{head_sha}`",
        f"- Failed review cycles (incl. this one): {decision.failed_cycles}",
    ]
    if run_url:
        lines.append(f"- Workflow run: {run_url}")
    if decision.restricted_files:
        lines.append(f"- Restricted paths touched: {', '.join(decision.restricted_files)}")
    if decision.escalate:
        lines.append("- **Escalated to a human maintainer (`needs-human`).**")
    if decision.reasons:
        lines += ["", "### Blocking reasons"] + [f"- {r}" for r in decision.reasons]
    for role, report in sorted(reports.items()):
        lines += ["", "---", ""]
        lines.append(render_report(report) if report else f"### Review {role}: missing\n")
    lines += [
        "",
        "_Implementation agent: address every blocking item, push to the same branch; both reviews "
        "re-run automatically on the new commit. A passing review of an older commit is invalid._",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- GitHub I/O


class NoRedirect(urllib.request.HTTPRedirectHandler):
    @override
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise ValueError("GitHub API redirects are forbidden")


class GitHub:
    def __init__(self, repository: str, token: str, api: str = "https://api.github.com") -> None:
        self.repository = repository
        self.token = token
        parsed = urlsplit(api)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("GitHub API must be an HTTPS origin")
        self.api = api.rstrip("/")
        self.origin = (parsed.scheme, parsed.hostname, parsed.port or 443)

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        url = path if path.startswith("http") else f"{self.api}{path}"
        parsed = urlsplit(url)
        if (
            (parsed.scheme, parsed.hostname, parsed.port or 443) != self.origin
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("refusing request to unexpected origin")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)  # noqa: S310  # nosec B310
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        # Exact HTTPS origin validated above; redirect handler rejects all redirects.
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(req, timeout=60) as resp:  # nosec B310
            payload = resp.read()
        return json.loads(payload) if payload else None

    def paged(self, path: str) -> list[Any]:
        items: list[Any] = []
        sep = "&" if "?" in path else "?"
        for page in range(1, 51):
            batch = self._request("GET", f"{path}{sep}per_page=100&page={page}")
            if not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
        return items

    def pr(self, number: int) -> dict[str, Any]:
        result: dict[str, Any] = self._request("GET", f"/repos/{self.repository}/pulls/{number}")
        return result

    def changed_files(self, number: int) -> list[str]:
        files = self.paged(f"/repos/{self.repository}/pulls/{number}/files")
        names: set[str] = set()
        for f in files:
            names.add(f["filename"])
            if f.get("previous_filename"):
                names.add(f["previous_filename"])  # renames out of restricted paths count too
        return sorted(names)

    def approvals(self, number: int) -> list[Approval]:
        reviews = self.paged(f"/repos/{self.repository}/pulls/{number}/reviews")
        return [
            Approval(r["user"]["login"], r["state"], r.get("commit_id") or "")
            for r in reviews
            if r.get("user")
        ]

    def comments(self, number: int) -> list[Comment]:
        raw = self.paged(f"/repos/{self.repository}/issues/{number}/comments")
        return [
            Comment(c["user"]["login"], c.get("body") or "", c["created_at"])
            for c in raw
            if c.get("user")
        ]

    def open_issue_refs(self, refs: Iterable[str]) -> set[str]:
        valid: set[str] = set()
        for ref in set(refs):
            try:
                issue = self._request("GET", f"/repos/{self.repository}/issues/{ref.lstrip('#')}")
            except urllib.error.HTTPError:
                continue
            if issue.get("state") == "open" and "pull_request" not in issue:
                valid.add(ref)
        return valid

    def set_status(
        self, sha: str, state: str, context: str, description: str, url: str | None
    ) -> None:
        body = {"state": state, "context": context, "description": description}
        if url:
            body["target_url"] = url
        self._request("POST", f"/repos/{self.repository}/statuses/{sha}", body)

    def comment(self, number: int, body: str) -> None:
        self._request("POST", f"/repos/{self.repository}/issues/{number}/comments", {"body": body})

    def add_labels(self, number: int, labels: list[str]) -> None:
        self._request(
            "POST", f"/repos/{self.repository}/issues/{number}/labels", {"labels": labels}
        )


def load_reports(reports_dir: Path) -> dict[str, dict[str, Any] | None]:
    reports: dict[str, dict[str, Any] | None] = {}
    for path in sorted(reports_dir.glob("review-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        role = data.get("role")
        if isinstance(role, str):
            # Ambiguous artifacts are invalid, regardless of filename ordering.
            reports[role] = None if role in reports else data
    return reports


def run_github(args: argparse.Namespace) -> int:
    trusted = Path(args.trusted_root).resolve()
    policy = load_policy(trusted / ".github/review/policy.json")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    gh = GitHub(args.repository, token)
    pr = gh.pr(args.pr)
    head = pr["head"]["sha"]
    if head != args.head:
        # The PR moved while reviews ran: never publish a verdict for a different commit.
        print(f"PR head moved ({args.head} -> {head}); not publishing.", file=sys.stderr)
        return 0
    reports: dict[str, dict[str, Any] | None] = {
        spec["role"]: None for spec in policy["required_reviews"]
    }
    reports.update(load_reports(Path(args.reports_dir)))
    refs: set[str] = set()
    for rep in reports.values():
        out = rep.get("model_output") if rep else None
        if isinstance(out, dict):
            refs.update(
                f["tracked_issue"]
                for f in out.get("findings", [])
                if isinstance(f, dict) and isinstance(f.get("tracked_issue"), str)
            )
    comments = gh.comments(args.pr)
    decision = decide(
        policy,
        head_sha=head,
        repository=args.repository,
        pr_number=args.pr,
        base_sha=pr["base"]["sha"],
        reports=reports,
        changed_files=gh.changed_files(args.pr),
        approvals=gh.approvals(args.pr),
        failed_cycles=count_failed_cycles(
            comments,
            current_head=head,
            humans=policy["human_maintainers"],
            reset_command=policy.get("commands", {}).get(
                "reset_cycles", "/semi-edge reset-review-cycles"
            ),
        ),
        open_issue_refs=gh.open_issue_refs(refs),
        is_draft=bool(pr.get("draft")),
    )
    body = render_comment(
        decision,
        head_sha=head,
        reports=reports,
        run_url=args.run_url,
        render_report=render_markdown,
    )
    body = redact(body, [token])
    print(body)
    if args.dry_run:
        return 0
    gh.comment(args.pr, body[:65000])
    labels = []
    if decision.escalate:
        labels.append("needs-human")
    if decision.restricted_files:
        labels.append("restricted-change")
    if labels:
        gh.add_labels(args.pr, labels)
    gh.set_status(
        head, decision.state, policy["status_context"], decision.description, args.run_url
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    gh = sub.add_parser("github")
    gh.add_argument("--reports-dir", required=True)
    gh.add_argument("--repository", required=True)
    gh.add_argument("--pr", required=True, type=int)
    gh.add_argument("--head", required=True)
    gh.add_argument("--run-url")
    gh.add_argument("--dry-run", action="store_true")
    gh.add_argument("--trusted-root", default=str(TRUSTED_ROOT_DEFAULT))
    args = parser.parse_args(argv)
    return run_github(args)


if __name__ == "__main__":
    raise SystemExit(main())
