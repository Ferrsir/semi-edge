# Engineering policy

SEMI-EDGE is paper-only. Ordinary approved development may proceed without per-PR human permission. Merging and deployment are separate actions.

## Where the rules live
`AGENTS.md` holds the operating rules, task board and review protocol that Claude Code and Codex
follow day to day (owner decision, PR #44). This policy holds the target-state controls and the
restricted actions. When the two disagree on process, `AGENTS.md` and ADR-004 govern until the
target state below is reached. Restricted actions (below) always apply.

## Current process (ADR-004, in force)
One task per issue, branch and worktree. Implement, add tests, validate, push and open a PR
linked to the issue. A reviewer from the other provider (Claude ↔ Codex) posts PASS/FAIL with
severity-ranked findings for the exact head SHA. Fixes go on the same branch and are re-reviewed.
After three failed review/fix cycles on a PR, escalate to the owner. The lead merges (squash)
when CI is green and the review is PASS with no open P0/P1; P2 deferrals need an open follow-up
issue. Agents may commit `AGENTS.md` board updates directly to `main`; every other change goes
through a PR.

## Target state (after #4, #5, #6 and #7)
Two independent reviews of the exact SHA from trusted, separately administered integrations:
Review A covers software correctness and architecture, Review B quantitative correctness,
security and financial safety. The implementation agent cannot approve its own work. Protected PR
merges, current-commit validation, no bypass, no force push or deletion, trusted check
provenance. No direct pushes to `main`. Auto-merge only after these controls are
demonstrated. Comments and local reports are evidence, not trusted checks.

## Restricted actions
Human authorization is required for governance or protection changes, reviewer requirements, credentials, live endpoints or trading permissions, risk-limit increases, and deployment of trading execution changes. This initial governance setup was explicitly requested; that authorization does not grant permanent authority to weaken controls. Keep administration credentials outside agent workflows. Human and agent identities must be distinct before identity-based approval can be trusted.

## Operation
Maintain milestones, issue ownership, prioritized dependencies, ADRs, test evidence, review history and release notes. Continue approved tasks within available execution and resource budgets. Do not invent a background worker. If orchestration is unavailable, leave a durable state with issue, branch, SHA, failed checks and next actions. Secrets never belong in prompts, reports or repository files.
