# Engineering setup report

Verified facts only, each with how it was checked. Last verified 2026-11-13 against `main` `5da2599`.
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
- The repository is private: `gh api repos/Ferrsir/semi-edge --jq .private` → `true`.
- Branch protection and rulesets are unavailable: `gh api repos/Ferrsir/semi-edge/branches/main/protection`
  → HTTP 403 "Upgrade to GitHub Pro or make this repository public". No plan or visibility change
  has been made. Tracked in #4.
- No repository secrets exist: `gh secret list` is empty, so the AI review workflow cannot run
  (#6).
- Agents act through the owner's GitHub account (`gh auth status` → `Ferrsir`, with admin rights).
  Agent and human identities are not distinct, so identity-based approval cannot be trusted, and
  the owner cannot formally approve a PR that the same account opened (#5).

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
1. #4: obtain branch protection (GitHub Pro or public visibility) and apply the main ruleset.
2. #5: a least-privileged agent GitHub identity, distinct from the owner's account.
3. #6: model credentials for isolated review runtimes, outside feature-accessible workflows.
4. #7: once 1–3 are done, test PRs that must be refused, plus one clean path, recorded here
   with SHAs. Only after that may the ADR-004 waiver be revisited.

History: the bootstrap report for PR #33 (2026-10-30) is in git history (`git log --
docs/governance/ENGINEERING_SETUP_REPORT.md`).
