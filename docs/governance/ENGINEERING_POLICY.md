# Engineering policy

SEMI-EDGE is paper-only. Ordinary approved development may proceed without per-PR human permission. Merging and deployment are separate actions.

## Development lifecycle
Select an approved issue; record ownership and dependencies. Use an isolated feature worktree. Implement, add relevant tests, validate, commit, push, and open a PR linked to the issue. Obtain two independent reviews of the exact proposed SHA. The implementation agent cannot approve its own work. Review A covers software correctness and architecture; Review B covers quantitative correctness, security and financial safety. Prefer different providers when available. Each report must identify role, reviewer, PR, SHA, PASS/FAIL, severity, evidence and required changes.

Fix blocking findings on the same branch and regenerate both reviews after every code change. Allow at most three failed review/fix cycles, then escalate. Do not equate comments or local reports with trusted GitHub checks.

## Merge requirements
CI, security, integration, trading-safety, and both independently issued reviews must pass for the latest revision. No unresolved P0/P1 findings; P2 deferrals require an open follow-up issue under policy. Require protected PR merges, current-commit validation, no bypass, no force push or deletion, and trusted check provenance. Enable GitHub auto-merge only after these controls are demonstrated. Do not push to main directly.

## Restricted actions
Human authorization is required for governance or protection changes, reviewer requirements, credentials, live endpoints or trading permissions, risk-limit increases, and deployment of trading execution changes. This initial governance setup was explicitly requested; that authorization does not grant permanent authority to weaken controls. Keep administration credentials outside agent workflows. Human and agent identities must be distinct before identity-based approval can be trusted.

## Operation
Maintain milestones, issue ownership, prioritized dependencies, ADRs, test evidence, review history and release notes. Continue approved tasks within available execution and resource budgets. Do not invent a background worker. If orchestration is unavailable, leave a durable state with issue, branch, SHA, failed checks and next actions. Secrets never belong in prompts, reports or repository files.
