# Protection activation runbook (#4)

Status on 2026-11-13: the repository is public, so rulesets are available on the free plan. The
earlier HTTP 403 ("Upgrade to GitHub Pro or make this repository public") no longer applies.
`main` has no ruleset yet (`gh api repos/Ferrsir/semi-edge/rulesets --jq length` → `0`). Until one
is active, the merge rules in `AGENTS.md` are followed by process only.

Only the owner applies or changes the ruleset. The agent identity `Ferrisfy` has no admin rights
and must not hold a credential that does (#5).

## The ruleset

`docs/governance/main-ruleset.json` targets the default branch with no bypass actors:

| Rule | Effect |
|---|---|
| `deletion` | `main` cannot be deleted. |
| `non_fast_forward` | No force pushes. |
| `required_linear_history` | No merge commits; squash merges only (also set below). |
| `pull_request` | Every change to `main` goes through a PR. Code owners (`.github/CODEOWNERS`, the owner) must approve PRs that touch their paths. Stale approvals are dismissed on a new push, so a commit pushed after your approval needs approving again. Last-push approval is off (F-165): both agents push as `Ferrisfy`, so it would make every PR, board PRs included, wait for the owner. Other PRs need 0 approvals: the cross-provider review stays a PR comment, as ADR-004 defines it. |
| `required_status_checks` | `ci-required`, `integration-required`, `security-required` and `trading-safety-required` must pass, published by the GitHub Actions app (id 15368). The branch does not have to be up to date with `main` (see below). |

`tests/governance/test_main_ruleset.py` keeps the file fail closed: no bypass actors, deletion and
force push blocked, code-owner review on, and every required check name published by a job in
`.github/workflows/`. A renamed aggregate job would otherwise leave a required check that never
reports, and every PR would block.

Not included yet: the two independently issued AI review checks. They need the trusted review
integration in #6, published by a separately controlled identity rather than the shared GitHub
Actions app. Requiring them now would block every merge.

## Decisions made (owner delegated them to the lead, 2026-11-13)

1. **Board updates.** With a PR required, agents cannot push straight to `main`, and `AGENTS.md`
   is code-owned. The board, queues and discussion log therefore move to `BOARD.md`, which is not
   code-owned (#93, PR #94), so a board-only PR merges on green checks without owner approval. The
   rules stay in code-owned `AGENTS.md`. **#94 must merge before this ruleset is applied.**
2. **No strict up-to-date requirement.** `strict_required_status_checks_policy` is `false`. With
   strict on, every open PR must rebase and rerun all CI after each merge, and board PRs would make
   that happen many times a day, against the Actions-minutes cut in #85. The trade-off: two PRs that
   each pass on their own can break `main` together. The workflows also run on every push to `main`,
   so such a break shows up as a red `main` run right after the merge and is fixed forward. Revisit
   if that happens more than rarely.

## Apply (owner, browser)

1. Settings → Rules → Rulesets → New ruleset → **Import a ruleset**, and choose
   `docs/governance/main-ruleset.json` from `main`.
2. Check that the bypass list is empty, then create it.

Or, from a terminal logged in as the owner (not an agent shell):

```
gh api -X POST repos/Ferrsir/semi-edge/rulesets --input docs/governance/main-ruleset.json
```

## Verify (any identity)

```
gh api repos/Ferrsir/semi-edge/rulesets --jq '.[] | {name, enforcement}'
gh api repos/Ferrsir/semi-edge/rules/branches/main --jq '[.[].type]'
```

Then run the #7 test PRs. Each must be refused: a failing required check; a code-owned path with
no owner approval; an approval made stale by a new push; a direct push and
a force push to `main` as `Ferrisfy`. Also run one clean PR that merges, and one board-only PR that merges without approval. Record the PR numbers,
head SHAs and check URLs in `ENGINEERING_SETUP_REPORT.md` and on #7. Never treat a merge test as
evidence while the ruleset is absent. No such demonstration has been done yet. Only after it may
the ADR-004 waiver be revisited.
