# Running Claude Code and Codex on SEMI-EDGE (Windows terminal)

Both agents work from the same repository and the same rules file, `AGENTS.md`. Codex reads
`AGENTS.md` by itself; Claude Code reads `CLAUDE.md`, which imports `AGENTS.md`. Edit the rules in
`AGENTS.md` only.

All commands are for **PowerShell** in Windows Terminal unless marked otherwise.

## 0. Migration off `.t3` (status 2026-11-13)

T3 orchestration is retired. Claude Code and Codex CLI run directly in a terminal and coordinate
through `AGENTS.md`, GitHub issues and PRs. The primary clone is `C:\Users\ferri\projects\semi-edge`,
with worktrees in `C:\Users\ferri\projects\semi-edge.worktrees\`. The old
`C:\Users\ferri\.t3\projects\semi-edge` clone and its worktrees are a separate clone. Nothing was
moved out of them, so no git worktree paths needed repair. They are kept in place until the
checklist below passes.

| `.t3` worktree | State found 2026-11-13 | Where the work is now |
|---|---|---|
| `offline-ingestion` (#42) | committed and pushed | PR #45 |
| `spec23` (`feature/15-phase2-4-spec`, `badef76`) | committed, not pushed | pushed to `origin` |
| `account` (`feature/13-paper-account`) | F-009 fix uncommitted | copied as a patch → PR #39 at `da04e38`; the `.t3` copy is untouched |
| `data` (`feature/9-data-layer`, ahead 2) | pre-rebase copies of the PR #36 commits | superseded by `origin/feature/9-data-layer` |
| `.claude/worktrees/wf_12ab529d-ad2-2` | two untracked early indicator folders | superseded by PR #48; left in place |

Every `.t3` branch tip is also kept in the primary clone as a local ref:
`git -C C:\Users\ferri\projects\semi-edge for-each-ref refs/t3-backup`.

Before deleting `C:\Users\ferri\.t3\projects\semi-edge*` (owner only, after PRs #39 and #45 merge):

```powershell
$t3 = "C:\Users\ferri\.t3\projects\semi-edge"
git -C $t3 worktree list
foreach ($w in (git -C $t3 worktree list --porcelain | Select-String '^worktree ' | ForEach-Object { $_.Line.Substring(9) })) {
  "== $w"; git -C $w status --short; git -C $w log --oneline '@{u}..HEAD'
}
```

Only `account` (uncommitted, superseded by `da04e38`), `data` (superseded) and the `wf_...` scratch
worktree should report anything. Make sure no agent, Codex included, is still running inside
`.t3` before removing it.

## 1. Install the prerequisites (once)

```powershell
winget install --id Git.Git -e            # Git for Windows (Claude Code needs Git Bash)
winget install --id OpenJS.NodeJS.LTS -e  # Node 18+ for Codex CLI
winget install --id GitHub.cli -e         # gh, for PRs and issues
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # uv
```

Close and reopen the terminal so the new commands are on PATH, then:

```powershell
gh auth login                    # GitHub.com -> HTTPS -> log in with a browser
uv python install 3.12           # the project needs Python >= 3.12
```

## 2. Install the two agents (once)

```powershell
irm https://claude.ai/install.ps1 | iex     # Claude Code
npm install -g @openai/codex                # Codex CLI
claude --version
codex --version
```

First run of each asks you to sign in: `claude` opens a browser for your Claude account, `codex`
offers "Sign in with ChatGPT". Never paste API keys into either chat.

## 3. Get the repository into a normal folder

Pick one home for the project and stop using `.t3`:

```powershell
mkdir C:\Users\ferri\projects -Force
cd C:\Users\ferri\projects
gh repo clone Ferrsir/semi-edge
cd semi-edge
uv sync --locked                 # add --system-certs if a corporate/antivirus proxy breaks TLS
uv run pytest -q                 # sanity check: should pass offline with no credentials
```

Paper credentials (only when you are ready for real paper runs): `copy .env.example .env`, then
open `.env` in Notepad and type the PAPER keys there yourself. `.env` is git-ignored and Claude
Code is denied from reading it (`.claude/settings.json`).

## 4. One worktree per agent

Two agents editing the same folder at once will trample each other. Give each task its own
worktree next to the main clone:

```powershell
cd C:\Users\ferri\projects\semi-edge
git fetch origin
git worktree add ..\semi-edge.worktrees\signals -b feature/11-signals-strategy origin/feature/11-signals-strategy
git worktree add ..\semi-edge.worktrees\offline-ingestion -b feature/42-offline-ingestion origin/feature/42-offline-ingestion
git worktree list
```

Run `uv sync --locked` once inside each new worktree.

## 5. Run them side by side

Open two Windows Terminal tabs (Ctrl+Shift+T).

**Tab 1, Claude Code (lead / integrator):**

```powershell
cd C:\Users\ferri\projects\semi-edge.worktrees\signals
claude
```

Good first prompt: `Read AGENTS.md, then refresh GitHub state with gh and tell me what's open.`
Useful commands inside Claude Code: `/memory` (shows that CLAUDE.md + AGENTS.md loaded),
`/permissions`, `/plugin`, `/clear`. Shift+Tab cycles permission modes; stay in the default
"ask" mode for anything touching `src/semiedge/safety/**` or `config/risk.yaml`.

**Tab 2, Codex (auditor / implementer):**

```powershell
cd C:\Users\ferri\projects\semi-edge.worktrees\offline-ingestion
codex
```

Good first prompt: `Read AGENTS.md. Finish issue #42 on this branch: rerun validation, fix
findings, open a PR linked to #42.` Inside Codex, `/approvals` sets how much it can do without
asking, and `/review` reviews the current diff.

## 6. The handoff loop between them

1. An agent finishes a task, pushes its branch and opens a PR (`gh pr create`).
2. Cross-provider review, per `AGENTS.md` / ADR-004:
   - Claude PR → in the Codex tab: `Review PR #NN against AGENTS.md. Post PASS/FAIL with findings
     on the PR with gh.` (Use a separate review worktree: `git worktree add
     ..\semi-edge.worktrees\review-NN origin/<branch>`.)
   - Codex PR → same prompt in the Claude tab.
3. Fixes go back to the author's worktree. Merge only when CI is green and the review is PASS.
4. Both agents write status to the `AGENTS.md` board and discussion log, so a fresh session in
   either tool can pick up where the other stopped. When an agent hits a usage limit it commits
   WIP, pushes, and writes a `Handoff:` line.

## 7. Optional: the day-trader skill (research only)

`Jakeschincariol/day-trader-skill` is an MIT pack of 11 Claude skills that read free public data
(Yahoo Finance, Nasdaq Trader, SEC EDGAR, House disclosures) with plain Python. It is **not**
part of SEMI-EDGE; see "Third-party skills" in `AGENTS.md`.

Install it project-locally **without** the broker skill:

```powershell
cd C:\Users\ferri\projects
git clone https://github.com/Jakeschincariol/day-trader-skill.git
$skills = "C:\Users\ferri\projects\semi-edge\.claude\skills"
mkdir $skills -Force
Get-ChildItem day-trader-skill -Directory -Filter "trader-*" |
  Where-Object { $_.Name -notin @("trader-broker", "trader-pelosi") } |
  ForEach-Object { Copy-Item $_.FullName $skills -Recurse -Force }
```

Do not use the `/plugin install day-trader-skill@...` route: it installs all 11, including
`trader-broker`. Restart `claude` in the main clone and type `/trader-scanner` etc. The skills
call `python3`; on Windows, if that fails, run `uv python install 3.12` or use `py -3`.

Rules when using it:
- Never set up the Alpaca MCP server for Claude Code on this project, and never set the skill's
  `"paper"` flag to false. Order tools are denied in `.claude/settings.json` anyway.
- Its output folder `trader/` is git-ignored. Its numbers (Time Machine odds, scanner hits) are
  not SEMI-EDGE evidence and never become fixtures, tests or the earnings source (#20).
- An idea worth testing (e.g. opening-range breakout) becomes a spec in `docs/specifications/`
  first, then a backtest. No tuning `momentum_v1` to fit what the skill showed.
- `trader-news` / `trader-analyst` ask for an SEC user agent with a name and email; put it in the
  `SEC_USER_AGENT` environment variable, not in a committed file.
