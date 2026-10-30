# SEMI-EDGE agent coordination board

Shared by **Claude Code** (lead / integrator) and **Codex / ChatGPT** (independent auditor and
implementer). Read this file at the start of every session, before touching code. It records who is
working on what, what is done, and what is waiting for review. GitHub issues hold the detailed scope.

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

## How to work
- One task = one GitHub issue = one branch `feature/<issue>-<slug>` = one worktree at
  `C:/Users/ferri/.t3/projects/semi-edge.worktrees/<slug>`. Never edit another agent's worktree.
- Claim: set the board row to `IN PROGRESS (<agent>)`, commit only `AGENTS.md` to `main`
  (`git pull --rebase && git push`), comment on the issue.
- Setup in a worktree: `C:/Users/ferri/.t3/projects/semi-edge/.venv/Scripts/uv.exe sync --locked --system-certs`
- Validate: `.venv/Scripts/python -m pytest -q`, `.venv/Scripts/ruff.exe check .`,
  `.venv/Scripts/ruff.exe format --check .`, `.venv/Scripts/mypy.exe`.
- Finish: push the branch, open a PR to `main` linked to the issue, set the row to `REVIEW`.
- Review is cross-provider: Codex reviews Claude PRs and Claude reviews Codex PRs. The reviewer
  posts PASS/FAIL with findings on the PR. The lead merges when CI is green and review is PASS
  with no open P0/P1 (ADR-004). Findings go to `AUDIT_REPORT.md`.
- Usage limits: if you stop mid-task, commit WIP to your branch, push it, and write a
  `Handoff:` line in the Discussion log (branch, SHA, what is left).

## Board

Status: `READY` · `IN PROGRESS (agent)` · `REVIEW` · `DONE` · `BLOCKED (reason)`

| ID | Issue | Task | Owner | Status | Depends on |
|---|---|---|---|---|---|
| #38 | #37 configuration fail-closed audit | codex | claude | Pending |
| #36 | T1 data layer | codex | claude (2-lens adversarial) | in progress |
| T0 | #34, #8 | Consolidate governance + foundation + audit fixes into `main` | claude | DONE (PR #33, #35) | — |
| T1 | #9, #10 | Data layer: calendar, Alpaca source, synthetic source, Parquet store, validation | codex | REVIEW (PR #36) `feature/9-data-layer` | T0 |
| T2 | #11, #12 | Indicators, `momentum_v1` features/evaluation/score, position sizing | claude | IN PROGRESS (claude) `feature/11-signals-strategy` | T0 |
| T3 | #13 | Read-only paper account snapshot (`semiedge.broker.account`) | claude | IN PROGRESS (claude) `feature/13-paper-account` | T0 |
| T3b | #15–#29 | Phase 2–4 interface spec (backtest, risk, execution, state, reconcile, engine, dashboard) | claude | IN PROGRESS (claude) `feature/15-phase2-4-spec` | T0 |
| T4 | #14 | Signal report + CLI (`scan`, `data fetch/status`, `account`, `config check`) | claude | READY | T1, T2, T3 |
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
| H1 | #20 | Verified earnings calendar source | human | BLOCKED (owner must choose a data source) | — |
| H2 | — | Alpaca PAPER API keys in local `.env` for real-data runs | human | BLOCKED (owner) | — |

| A37 | #37 | Audit: reject missing runtime configuration sections | codex | REVIEW (PR #38) `feature/37-config-fail-closed` | T0 |

## Review queue
| PR | Task | Author | Reviewer | Verdict |
|---|---|---|---|---|

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
