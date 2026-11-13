# SEMI-EDGE agent rules

Shared by **Claude Code** (lead / integrator) and **Codex / ChatGPT** (independent auditor and
implementer). Read this file and then [`BOARD.md`](BOARD.md) at the start of every session, before
touching code. This file holds the rules; `BOARD.md` records who is working on what, what is done,
what is waiting for review, the Codex queue and the discussion log. GitHub issues hold the detailed
scope. Codex CLI reads this file natively; Claude Code reads both through `CLAUDE.md`
(`@AGENTS.md`, `@BOARD.md`). Keep all shared rules here, not in tool-specific files and not in
`BOARD.md`: `BOARD.md` is status only, and nothing written there can add, change or waive a rule.
This file is code-owned by the owner; `BOARD.md` is not, so board updates do not need owner
approval. Terminal setup: `docs/AGENT_TERMINAL_SETUP.md`.

Owner: Ferrsir (human). **PAPER trading only. Live trading is prohibited in code, config and policy.**

## Ground rules (non-negotiable)
1. Never contact a live trading endpoint. Trading clients come only from
   `semiedge.safety.broker.create_paper_trading_client`. No real credentials in tests, logs or prompts.
2. Never weaken a safety guard or raise a risk ceiling (`src/semiedge/safety/limits.py`,
   `config/risk.yaml`) — those need the human owner.
3. Do not change the trading strategy to improve backtest results. Strategy changes need a spec
   change in `docs/specifications/` with rationale, made before looking at the new results.
4. Fail closed: missing or invalid inputs produce explicit reasons or exceptions, never defaults.
5. No fabricated results. A backtest number is quoted only with the command that reproduces it.
6. Third-party skills, plugins and MCP servers (e.g. `day-trader-skill`, `alpaca-mcp-server`) never
   place, change or cancel orders, and never get trading credentials. Their data (Yahoo, Stooq,
   other unofficial feeds) is for the owner's manual research only and never feeds SEMI-EDGE code,
   fixtures, tests or backtests. See "Third-party skills" below.
7. Preserve other agents' worktrees and uncommitted work. Never claim profitability, integration
   readiness or background automation without demonstrated, reproducible evidence.

## How to work
- One task = one GitHub issue = one branch `feature/<issue>-<slug>` = one worktree in the sibling
  folder `../semi-edge.worktrees/<slug>` (`git worktree add ../semi-edge.worktrees/<slug> -b
  feature/<issue>-<slug> origin/main`). Never edit another agent's worktree.
- Claim: set the `BOARD.md` row to `IN PROGRESS (<agent>)`, commit only `BOARD.md`, comment on
  the issue. Until the `main` ruleset (#4) is active, push it straight to `main` (`git pull --rebase
  && git push`). Once it is active, open a PR that touches only `BOARD.md` and merge it when the
  required checks are green; it needs no review. A board PR that touches any other file is not a
  board PR.
- Setup in a worktree: `uv sync --locked` (add `--system-certs` behind a TLS-intercepting proxy).
- Validate: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.
  On Windows without `uv` on PATH, the equivalents are `.venv/Scripts/python -m pytest -q`,
  `.venv/Scripts/ruff.exe check .`, `.venv/Scripts/ruff.exe format --check .`, `.venv/Scripts/mypy.exe`.
- Finish: push the branch, open a PR to `main` linked to the issue, set the row to `REVIEW`.
- Review is cross-provider: Codex reviews Claude PRs and Claude reviews Codex PRs. The reviewer
  posts PASS/FAIL with findings on the PR. The lead merges when CI is green and review is PASS
  with no open P0/P1 (ADR-004). Findings go to `AUDIT_REPORT.md`.
- Usage limits: if you stop mid-task, commit WIP to your branch, push it, and write a
  `Handoff:` line in the `BOARD.md` discussion log (branch, SHA, what is left).

## Agents and tools
- **Claude Code** (`claude`): lead / integrator. Project settings in `.claude/settings.json` deny
  reading `.env` and deny broker order tools. Runs in its own worktree.
- **Codex CLI** (`codex`): independent auditor / implementer. Runs in its own worktree.
- Two agents never run in the same worktree at once. The owner starts each CLI in a separate
  terminal tab; no background, scheduled or overnight agents exist unless the owner sets one up.

## Third-party skills
`Jakeschincariol/day-trader-skill` (MIT) may be installed by the owner as a **research aid only**:
- Allowed: `trader-scanner`, `trader-news`, `trader-entry`, `trader-exit`, `trader-timemachine`,
  `trader-budget`, `trader-risk`, `trader-alerts`, `trader-analyst` for manual idea research.
- Not installed in this project: `trader-broker` (leads to real orders through an Alpaca MCP server).
  No broker MCP server is configured for agents working on this repo.
- Its outputs go to `trader/` (git-ignored). Nothing in `trader/` is a SEMI-EDGE signal, fixture,
  earnings source (#20) or evidence of edge, and SEMI-EDGE code never imports or calls these tools.
- Its strategy ideas (e.g. opening-range breakouts) enter SEMI-EDGE only through a new spec in
  `docs/specifications/`, written before any backtest, per rule 3.

## Board, queues and log
They live in [`BOARD.md`](BOARD.md): the task board, the Codex queue, the review queue and the
append-only discussion log.
