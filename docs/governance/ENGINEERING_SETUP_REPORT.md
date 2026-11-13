# Engineering setup report

Verified facts only, each with how it was checked. Last verified 2026-11-13 against `main` `7bf7437`.
When a fact changes, update it here with the date and the check.

## Operating process in force
- **ADR-004** (`docs/DECISIONS.md`, 2026-10-30): the owner waived the branch-protection and
  trusted-review-issuer requirements and delegated merging to the lead agent. A PR merges when CI
  is green and a cross-provider review recorded on the PR (Claude ↔ Codex) is PASS with no open
  P0/P1. The day-to-day rules live in `AGENTS.md` (owner decision, PR #44).
- Board updates are committed by agents directly to `main`, touching only `AGENTS.md`, as
  `AGENTS.md` instructs. Every other change goes through a PR.
- Merged under this process: #38, #41 and #39. GitHub shows no `semi-edge/ai-review-gate` status
  on them (`gh api repos/Ferrsir/semi-edge/commits/<sha>/status` → `total_count: 0`). The PR
  comments hold the review record.

## Verified external blockers
- The repository is public since 2026-11-13 (owner decision, to unblock Actions and rulesets):
  `gh api repos/Ferrsir/semi-edge --jq .visibility` → `public`.
- `main` has no protection yet: `gh api repos/Ferrsir/semi-edge/rulesets --jq length` → `0`, and
  `gh api repos/Ferrsir/semi-edge/branches/main --jq .protected` → `false`. Public visibility makes
  rulesets available; applying one is the owner's step in #4. Until then the merge rules are
  followed by process only, and the agent identity below can push to `main`.
- No repository secrets are configured (`gh secret list` is empty as the agent identity), so the
  AI review workflow cannot run (#6).

## Agent identity (issue #5), verified 2026-11-13
- Agents authenticate as the bot account `Ferrisfy` through `GH_TOKEN` (classic token, `repo` and
  `workflow` scopes), loaded by the owner's agent shell. `gh auth status` → `Ferrisfy (GH_TOKEN)`.
- `gh api repos/Ferrsir/semi-edge --jq .permissions` → `admin: false, maintain: false, push: true`.
  The agent identity cannot change settings, protections or visibility.
- The owner account `Ferrsir` is logged out of `gh` on the agent PCs; approvals, merges of
  restricted PRs and settings changes happen in the owner's browser.
- `.github/review/policy.json`: `human_maintainers` is `["Ferrsir"]` and `agent_identities` is
  `["Ferrisfy"]`. `load_policy` rejects a login listed in both, an empty human list, and missing
  or malformed identity lists (`tests/governance/test_review_gate.py`).
- Remaining limits: the bot still has push access, so it can push to `main` and merge PRs until
  the #4 ruleset is active. Codex and Claude share this one identity, so GitHub cannot tell
  them apart; the PR comment labels (`Claude review`, `Codex review`) carry that distinction.

## CI (issue #2), demonstrated on real PRs
`ci`, `integration`, `trading-safety` and `security` run on every PR. Each ends in a `*-required`
aggregate job that fails unless every needed job succeeded (a skipped or cancelled job fails it).
Example: PR #48 has 16/16 checks green (`gh pr checks 48`). Workflow permissions default to
`contents: read` or `{}`, and every third-party action is pinned to a full commit SHA.

## AI review orchestration (issue #3): disabled prototype
`.github/workflows/review-orchestration.yml` is `workflow_dispatch` only, and its context job is
`if: false`. It has never run (`gh run list --workflow review-orchestration.yml` is empty). The
gate evaluator (`scripts/governance/review_gate.py`) is unit-tested (`uv run pytest -q
tests/governance`). The ordinary Actions status token is spoofable by other same-repository
workflows, so this workflow is not an enforceable independent review integration until a
separately administered identity publishes the check.

## Owner actions required
1. #4: apply the main ruleset (`docs/governance/BRANCH_PROTECTION.md`); public visibility now allows it.
2. #5: done except closing the issue (see "Agent identity" above).
3. #6: model credentials for isolated review runtimes, outside feature-accessible workflows.
4. #7: once 1–3 are done, test PRs that must be refused, plus one clean path, recorded here
   with SHAs. Only after that may the ADR-004 waiver be revisited.

History: the bootstrap report for PR #33 (2026-10-30) is in git history (`git log --
docs/governance/ENGINEERING_SETUP_REPORT.md`).
