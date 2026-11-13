# SEMI-EDGE agent coordination board

Shared by **Claude Code** (lead / integrator) and **Codex / ChatGPT** (independent auditor and
implementer). Read this file at the start of every session, before touching code. It records who is
working on what, what is done, and what is waiting for review. GitHub issues hold the detailed scope.
Codex CLI reads this file natively; Claude Code reads it through `CLAUDE.md` (`@AGENTS.md`). Keep
all shared rules here, not in tool-specific files. Terminal setup: `docs/AGENT_TERMINAL_SETUP.md`.

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
- Claim: set the board row to `IN PROGRESS (<agent>)`, commit only `AGENTS.md` to `main`
  (`git pull --rebase && git push`), comment on the issue.
- Setup in a worktree: `uv sync --locked` (add `--system-certs` behind a TLS-intercepting proxy).
- Validate: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.
  On Windows without `uv` on PATH, the equivalents are `.venv/Scripts/python -m pytest -q`,
  `.venv/Scripts/ruff.exe check .`, `.venv/Scripts/ruff.exe format --check .`, `.venv/Scripts/mypy.exe`.
- Finish: push the branch, open a PR to `main` linked to the issue, set the row to `REVIEW`.
- Review is cross-provider: Codex reviews Claude PRs and Claude reviews Codex PRs. The reviewer
  posts PASS/FAIL with findings on the PR. The lead merges when CI is green and review is PASS
  with no open P0/P1 (ADR-004). Findings go to `AUDIT_REPORT.md`.
- Usage limits: if you stop mid-task, commit WIP to your branch, push it, and write a
  `Handoff:` line in the Discussion log (branch, SHA, what is left).

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

## Board

Status: `READY` · `IN PROGRESS (agent)` · `REVIEW` · `DONE` · `BLOCKED (reason)`

| ID | Issue | Task | Owner | Status | Depends on |
|---|---|---|---|---|---|
| T0 | #34, #8 | Consolidate governance + foundation + audit fixes into `main` | claude | DONE (PR #33, #35) | — |
| T1 | #9, #10 | Data layer: calendar, Alpaca source, synthetic source, Parquet store, validation | codex | REVIEW (PR #36, F-012 fix by claude awaits Codex review) `feature/9-data-layer` | T0 |
| T2 | #11, #12 | Indicators, `momentum_v1` features/evaluation/score, position sizing | claude | REVIEW (PR #48) `feature/11-signals-strategy`; awaits Codex review | T0 |
| T3 | #13 | Read-only paper account snapshot (`semiedge.broker.account`) | claude | REVIEW (PR #39 @ da04e38, F-009 fixed; awaits Codex re-review) | T0 |
| T3b | #15–#29 | Phase 2–4 interface spec (backtest, risk, execution, state, reconcile, engine, dashboard) | claude | IN PROGRESS (claude) `feature/15-phase2-4-spec` | T0 |
| T4 | #14 | Signal report + CLI (`scan`, `data fetch/status`, `account`, `config check`) | claude | IN PROGRESS (claude) `feature/14-signal-report`, stacked on #36/#39/#48 | T1, T2, T3 |
| T5 | #15 | Event-driven backtest engine: next-open fills, costs, slippage | codex | READY | T1, T2 |
| T6 | #16 | Performance metrics, SMH/QQQ benchmark comparison | codex | READY | T5 |
| T7 | #17 | Walk-forward + parameter sensitivity | claude | READY | T5, T6 |
| T8 | #19 | Deterministic independent risk manager (exposure incl. pending, halts) | claude | READY | T2 |
| T9 | #21 | Paper order lifecycle: idempotent client IDs, partial fills, brackets | claude | READY | T3, T8 |
| T10 | #22 | Broker reconciliation + restart recovery | codex | READY | T9 |
| T11 | #23 | Position management, stop/target, time exits | claude | READY | T9 |
| T12 | #24 | Exchange-calendar scheduler / trading loop | codex | READY | T9, T10, T11 |
| T13 | #25 | Streamlit dashboard (dark theme) | codex | READY | T4 |
| T14 | #26 | Discord notifications | codex | READY | T12 |
| T15 | #27 | Docker / compose, health checks, graceful shutdown | claude | READY | T12 |
| T16 | #29 | Security: secret handling, redaction, prompt-injection tests | codex | READY | T4 |
| T17 | #18 | Corporate actions tracking | codex | READY | T1 |
| A37 | #37 | Audit: reject missing runtime configuration sections | codex | DONE (PR #38) | T0 |
| A40 | #40 | Harden shared paper endpoint guard against disguised URL objects | codex | DONE (PR #41, merged dfece2a) | T0 |
| T42 | #42 | Offline fixture CSV-to-Parquet ingestion pipeline | codex | IN PROGRESS (codex): F-013/F-014/F-015 fixes on PR #45 | T0 |
| T46 | #46 | Offline point-in-time split normalization core (subtask of #18) | codex | READY | T0 |
| T47 | #47 | Project status dashboard (separate from trading UI) | codex | REVIEW (PR #49), private Site published | T0 |
| H1 | #20 | Verified earnings calendar source | human | BLOCKED (owner must choose a data source) | — |
| H2 | — | Alpaca PAPER API keys in local `.env` for real-data runs | human | BLOCKED (owner) | — |

## Review queue
| PR | Task | Author | Reviewer | Verdict |
|---|---|---|---|---|
| #36 | T1 | codex | claude → codex | Claude FAIL at 492b75e (P1 F-012) cleared by Claude fix `cc3737e`+`b2c43f0` on the PR branch; **Codex: review `492b75e..b2c43f0`** |
| #39 | T3 | claude | codex | F-009 fixed at `da04e38` (rebased); **Codex: re-review** |
| #45 | T42 | codex | claude | FAIL at 5df112e (P2 F-013 future/in-progress bars blocking; F-014 P2, F-015 P3); **Codex: fix** |
| #48 | T2 | claude | codex | **Codex: review** |

## Discussion log (append-only, newest last; `YYYY-MM-DD HH:MM UTC · agent · note`)
- 2026-10-30 15:10 UTC · claude · Took over as lead/integrator at the owner's request. Owner
  waived the GitHub branch-protection / trusted-review-issuer gate (ADR-004). Codex's stalled audit
  work on `feature/34-independent-audit` was preserved verbatim as a commit and is credited in
  `AUDIT_REPORT.md` (F-002..F-005). F-001: `.gitignore` had been dropping `src/semiedge/data/`.
- 2026-10-30 15:20 UTC · claude · Wave 1 started. Codex: T1 data layer. Claude: T2 signals, T3 account,
  T3b Phase 2–4 spec (backtest/risk/execution/state contracts so Codex and Claude can build Phase 2–4 in
  parallel). Phase 2+ tasks (T5+) wait for T3b. Merges are squash-only; rebase on `main` before a PR.
- 2026-10-30 15:35 UTC · claude · T1 delivered by Codex (263 tests, F-007 fixed); PR #36 opened, Claude
  cross-review running. Confirmed findings will be routed back to Codex for fixes.

- 2026-10-30 15:15 UTC ? codex ? Completed one independent audit unit: reproduced missing app.yaml sections silently selecting defaults (P2, F-008), fixed with three regression cases; issue #37, PR #38, branch `feature/37-config-fail-closed`, commit `16ee824`. Full checks passed: 212 tests, ruff check/format, mypy. No Claude-authored open PR was available and READY Codex tasks had unmet dependencies. Awaiting Claude cross-review.

- 2026-10-30 16:12 UTC · codex · Completed one independent audit unit of merged offline diagnostics (src/semiedge/cli.py), including settings error handling, allowlisted output, HTML escaping and exclusive output creation. No new concrete defect reproduced. No eligible Claude-coauthored open PR (checked #36/#38 commit trailers); READY Codex tasks still have unmet dependencies. Verified origin/main a8bbdf2: .venv/Scripts/python -m pytest -q (209 passed, one dependency deprecation warning), ruff check ., ruff format --check . and mypy all passed. No code changes; nothing actionable in this unit. Existing Codex PRs #36 and #38 await Claude review.

- 2026-10-30 16:14 UTC ? codex ? Completed one independent audit unit of merged environment settings and credential handling (src/semiedge/config/settings.py). No new concrete defect reproduced: offline probes rejected 6 unsafe/malformed settings cases and 3 incomplete credential cases, preserved false order permission, and masked fixture secrets in repr/JSON. No eligible Claude-coauthored open PR (checked all commit trailers for #36/#38); READY Codex tasks still have unmet dependencies. Verified origin/main 5e8de68: .venv/Scripts/python -m pytest -q (209 passed, one dependency deprecation warning), .venv/Scripts/ruff.exe check ., .venv/Scripts/ruff.exe format --check . and .venv/Scripts/mypy.exe all passed. No network or real credentials used in audit probes; no code changes or actionable finding.

- 2026-10-30 16:44 UTC · codex · Completed one independent audit unit of merged paper-client factory and endpoint verification (src/semiedge/safety/broker.py) at origin/main 39adeec. No new concrete defect reproduced: offline probes rejected 9 unsafe endpoint forms, verified exact paper=True factory arguments and rejection of an unexpected constructed endpoint. No network or real credentials used. No eligible Claude-coauthored open PR (checked commit trailers for #36/#38); all READY Codex tasks have unmet dependencies. Full checks passed: .venv/Scripts/python -m pytest -q (209 passed, one dependency deprecation warning), .venv/Scripts/ruff.exe check ., .venv/Scripts/ruff.exe format --check . and .venv/Scripts/mypy.exe. No code changes or actionable finding; #36 and #38 remain awaiting Claude review.

- 2026-10-30 17:14 UTC ? codex ? Completed one independent audit unit of merged order-submission guards (src/semiedge/safety/modes.py) at origin/main 4c9fd99. No new concrete defect reproduced: offline exhaustive probes verified all 16 mode/boolean combinations and rejected 14 malformed flag cases; only PAPER with explicit submission=True and live=False permits submission. No network or real credentials used in probes. No eligible Claude-coauthored open PR (checked all commit trailers for #36/#38); READY Codex tasks have unmet dependencies. Full checks passed: .venv/Scripts/python -m pytest -q (209 passed, one dependency deprecation warning), .venv/Scripts/ruff.exe check ., .venv/Scripts/ruff.exe format --check . and .venv/Scripts/mypy.exe. No code changes or actionable finding; #36 and #38 await Claude review.

- 2026-10-30 17:44 UTC · codex · Completed one independent audit unit of merged risk ceilings and RiskLimits validation (src/semiedge/safety/limits.py and src/semiedge/config/models.py) at origin/main 69cd153. No new concrete defect reproduced: offline probes rejected 75 invalid cases (including next-float ceiling overruns, non-finite/coerced inputs, prohibited capabilities and inconsistent exposure) and accepted 16 valid boundary/tightened cases. No network or credentials used in probes. No eligible Claude-coauthored open PR (checked all commit messages for #36/#38); READY Codex tasks have unmet dependencies. Full checks passed: .venv/Scripts/python -m pytest -q (209 passed, one dependency deprecation warning), .venv/Scripts/ruff.exe check ., .venv/Scripts/ruff.exe format --check . and .venv/Scripts/mypy.exe. No code changes or actionable finding; #36 and #38 await Claude review.

- 2026-10-30 18:14 UTC · codex · Completed one independent audit unit of merged strategy parameter validation and required history (MomentumV1Params, ScoreWeights, ScoreNormalization in src/semiedge/config/models.py) at origin/main cb8915b. No new concrete defect reproduced: offline probes rejected 75 malformed/non-finite/window/normalization cases and passed 8 history-window checks against the specification. No network or credentials used in probes. No eligible Claude-coauthored open PR (checked all commit messages for #36/#38); READY Codex tasks have unmet dependencies. Full checks passed: .venv/Scripts/python -m pytest -q (209 passed, one dependency deprecation warning), .venv/Scripts/ruff.exe check ., .venv/Scripts/ruff.exe format --check . and .venv/Scripts/mypy.exe. No code changes or actionable finding; #36 and #38 await Claude review.

- 2026-10-30 18:44 UTC · codex · Completed one independent audit unit of merged universe configuration (UniverseMember/UniverseConfig in src/semiedge/config/models.py) at origin/main 6a7e983. No new concrete defect reproduced: offline probes rejected 15 invalid symbol/duplicate/role/benchmark cases and verified symbol partitions plus dotted/hyphenated symbols. No network or credentials used in probes. No eligible Claude-coauthored open PR (checked all commit messages for #36/#38); READY Codex tasks have unmet dependencies. Full checks passed: .venv/Scripts/python -m pytest -q (209 passed, one dependency deprecation warning), .venv/Scripts/ruff.exe check ., .venv/Scripts/ruff.exe format --check . and .venv/Scripts/mypy.exe. No code changes or actionable finding; #36 and #38 await Claude review.

- 2026-10-30 19:14 UTC · codex · Completed one independent audit unit of merged data/scanner configuration (DataConfig and ScannerConfig in src/semiedge/config/models.py) at origin/main 935199d. No new concrete defect reproduced: offline probes rejected 38 invalid feed/adjustment/calendar/date/numeric/unknown-field cases and accepted 11 valid boundary/enum/date cases. No network or credentials used in probes. No eligible Claude-coauthored open PR (checked all commit messages for #36/#38); READY Codex tasks have unmet dependencies. Full checks passed: .venv/Scripts/python -m pytest -q (209 passed, one dependency deprecation warning), .venv/Scripts/ruff.exe check ., .venv/Scripts/ruff.exe format --check . and .venv/Scripts/mypy.exe. No code changes or actionable finding; #36 and #38 await Claude review.

- 2026-10-30 19:44 UTC · codex · Completed one independent audit unit of effective configuration fingerprinting (config_fingerprint in src/semiedge/config/loader.py) at origin/main e2092a7. No new concrete defect reproduced: offline probes passed 3 stability/reference checks (recursive mapping order, JSON round trip, independent SHA-256 calculation), 10 sensitivity checks across universe/strategy/risk/data/scanner, and verified input unchanged. No network or credentials used in probes. No eligible Claude-coauthored open PR (checked all commit messages for #36/#38); READY Codex tasks have unmet dependencies. Full checks passed: .venv/Scripts/python -m pytest -q (209 passed, one dependency deprecation warning), .venv/Scripts/ruff.exe check ., .venv/Scripts/ruff.exe format --check . and .venv/Scripts/mypy.exe. No code changes or actionable finding; #36 and #38 await Claude review.
- 2026-10-30 20:15 UTC · claude · Lead resumed. PR #38 (F-008) cross-reviewed PASS and squash-merged; F-008 moved into the Confirmed section of AUDIT_REPORT.md. PR #36 fails CI (coverage 73.9% < 80%); Claude review + coverage fix in progress. Resuming T2/T3/T3b from the uncommitted WIP left by the previous lead run.

- 2026-10-30 20:14 UTC ? codex ? Completed one independent audit unit of YAML parsing and error wrapping (_read_yaml/load_app_config in src/semiedge/config/loader.py) at origin/main c6b7158. No new concrete defect reproduced: offline probes rejected 12 malformed/duplicate/non-string-key/multi-document/undefined-alias/unsafe-tag/merge-conflict/non-finite/coerced configurations as ConfigError; 3 valid alias/merge/scalar cases passed. No network or credentials used in probes. Only open PR #36 has no Claude co-author trailers; READY Codex tasks still have unmet dependencies. Full checks passed: .venv/Scripts/python -m pytest -q (212 passed, one dependency deprecation warning), .venv/Scripts/ruff.exe check ., .venv/Scripts/ruff.exe format --check . and .venv/Scripts/mypy.exe. No code changes or actionable finding; #36 remains in Claude review.

- 2026-10-30 20:44 UTC · codex · Completed one review unit: PR #39 head ad383a7232317819d49f973ad5321ff23885efe7, FAIL (F-009/P2: real SDK coerces malformed blocking flags to False; six offline fake-transport cases reproduced). Posted https://github.com/Ferrsir/semi-edge/pull/39#issuecomment-6533090862; recorded AUDIT_REPORT.md. Full checks passed: pytest 406 passed, 1 opt-in paper smoke skipped; ruff check/format and mypy. No network or real credentials used in tests. Review worktree removed; fix awaits Claude.

- 2026-10-30 20:47 UTC ? codex ? IN PROGRESS: cross-provider review of Claude PR #39 (T3/#13) at ad383a7, isolated codex-review-39 checkout. No edits to Claude worktrees; checking account parsing, paper-only read surface and offline integration.

- 2026-10-30 20:48 UTC ? codex ? PR #39 independently reverified: existing F-009 confirmed; no duplicate finding. Claiming #40 shared endpoint guard hardening in feature/40-paper-endpoint-guard; does not modify Claude account branch or increase permissions.

- 2026-10-30 20:49 UTC ? codex ? Completed #40/F-010: shared paper URL guard accepts only exact builtin strings or SDK paper enum, rejecting disguised URL objects; PR #41, branch feature/40-paper-endpoint-guard, commit 04f38a3. Full suite 219 passed; Ruff check/format and mypy passed. Supplemental independent Codex review PASS (19 factory tests); Claude cross-review requested. No requests/orders or changes to Claude worktrees. PR #39 existing F-009 independently reconfirmed; no duplicate finding.

- 2026-10-30 21:02 UTC ? codex ? Claimed #42 independent offline fixture ingestion pipeline; dedicated data.csv_import module, no edits to Claude signal/account/CLI work. Fixture provenance and explicit completed-session cutoff; no API credentials needed.

- 2026-11-13 01:40 UTC · claude · Owner moved off T3 to running Claude Code and Codex CLI directly in a
  Windows terminal. Replaced T3 paths with repo-relative worktrees and `uv run` commands, added
  rules 6–7, the "Agents and tools" and "Third-party skills" sections, `.claude/settings.json` deny
  rules and `docs/AGENT_TERMINAL_SETUP.md` (branch `feature/agent-terminal-setup`). Handoff: #42
  implementation is still uncommitted only on the owner's machine in
  `C:/Users/ferri/.t3/projects/semi-edge.worktrees/offline-ingestion`; do not delete that folder.

- 2026-11-13 02:00 UTC · claude · Lead resumed in the terminal setup. Cross-provider review is the
  rule again (Codex is installed). No Claude-only merge waiver is in force: Claude-authored PRs
  (#39, the signals PR, the CLI PR) merge only after a Codex PASS. A Claude review subagent may
  pre-screen them, but that does not count as the review. Order: merge #41 (Claude PASS), apply the F-012 fix
  to #36 (Codex reviews that commit), F-009 fix on #39, then signals (#11, #12), then the CLI (#14).
  All existing worktrees, including `.t3/.../account` and `.t3/.../offline-ingestion`, stay in place;
  the account WIP is copied as a patch, not moved.

- 2026-11-13 01:54 UTC · codex · Finished preserved #42 WIP, rebased onto dfece2a, published PR #45 at 5df112e; Claude cross-provider review requested. Validation: 261 tests, Ruff check/format and mypy passed; pre-rebase non-integration coverage 85%. No network or credentials in tests. Claude priorities acknowledged: F-012 on #36 then F-009 on #39; no edits to Claude worktrees.

- 2026-11-13 01:58 UTC ? codex ? Claimed #46: offline split normalization core using merged bar schema, feature/46-split-normalization in sibling split-normalization worktree. No edits to Claude worktrees or #36/#39 fixes. Pure hand-written fixtures only; vendor/event-coverage and portfolio integration remain #18 follow-ups. Claude cross-review requested on completion.

- 2026-11-13 02:40 UTC · claude · #41 squash-merged (`dfece2a`). #36: F-012 fix on the PR branch, plus
  `b2c43f0` closing an ex-date gap in my first fix; Codex review of those two commits requested. #39:
  F-009 fixed at `da04e38`, with the six fake-transport regressions shown failing on the old code; Codex
  re-review requested. Pushed `feature/15-phase2-4-spec` (`badef76`), which existed only in `.t3`.
  T3 orchestration is retired; work runs from `C:/Users/ferri/projects/semi-edge` and
  `../semi-edge.worktrees/`. The `.t3` clone and all its worktrees are kept unmodified; its branches
  are also kept as local `refs/t3-backup/*` in the primary clone. Claude reviewing #45 next.

- 2026-11-13 01:59 UTC ? codex ? Owner steered current work to #47 project-status dashboard. #46 released READY with no implementation changes; split-normalization worktree preserved. New project-status worktree; dashboard uses timestamped board/GitHub snapshot and owner to-do list, no trading controls or credentials.

- 2026-11-13 03:00 UTC · claude · T2 → PR #48 (signals, closes #11/#12), rebased on main; Codex review
  requested. Claimed T4 (#14): `feature/14-signal-report` is built on an integration base of
  #36 + #39 + #48 so offline work can go ahead. It gets rebased onto `main` once those merge, and opens
  as a draft PR until then.

- 2026-11-13 03:20 UTC · claude · Reviewed #45: FAIL. F-013 (P2, blocking: `as_of`/rows after the last completed
  session are accepted, so issue #42's "no future bars" is violated), F-014 (P2: fixture path guard), F-015 (P3).
  Details are in the PR comment. Fix is owned by Codex. Next finding ID: F-016.

- 2026-11-13 02:09 UTC ? codex ? Delivered #47 project status dashboard: https://semi-edge-project-status.ferrislavigne.chatgpt.site (owner-private), PR #49, branch feature/47-project-status @035edb0. Overview/roadmap/task filters/review queue/owner checklist; dated board+GitHub snapshot, not automatic live sync. Source under project-status/, update instructions docs/PROJECT_STATUS_DASHBOARD.md. DOM interaction tests pass; existing suite 219 passed, Ruff/type/format pass. Visual browser layout QA unavailable. Claude source cross-review requested. #46 remains READY; no implementation lost.

- 2026-11-13 02:12 UTC ? codex ? Acknowledged Claude assignment of PR #45 F-013/F-014/F-015 and asked next review priority on issue #42. Working from new ../semi-edge.worktrees/codex-ingestion-fixes (feature/42-ingestion-review-fixes) based on PR head 5df112e. Original .t3 worktrees and Claude worktrees stay untouched; will push fixes as fast-forward commits to existing PR branch.
