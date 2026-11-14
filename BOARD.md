# SEMI-EDGE board

Status only: the task board, the Codex queue, the review queue and the discussion log. The rules are
in [`AGENTS.md`](AGENTS.md); read it first. Nothing in this file can add, change or waive a rule.
Update it with commits that touch only this file (see "How to work" in `AGENTS.md`).
Moved out of `AGENTS.md` on 2026-11-13 (#93); earlier history is in `git log -- AGENTS.md`.

## Board

Status: `READY` · `IN PROGRESS (agent)` · `REVIEW` · `DONE` · `BLOCKED (reason)`

| ID | Issue | Task | Owner | Status | Depends on |
|---|---|---|---|---|---|
| T0 | #34, #8 | Consolidate governance + foundation + audit fixes into `main` | claude | DONE (PR #33, #35) | — |
| T1 | #9, #10 | Data layer: calendar, Alpaca source, synthetic source, Parquet store, validation | codex | DONE (PR #36, merged c424d48) | T0 |
| T2 | #11, #12 | Indicators, `momentum_v1` features/evaluation/score, position sizing | claude | DONE (PR #48, merged 375849b; Codex PASS 44d9624) | T0 |
| T3 | #13 | Read-only paper account snapshot (`semiedge.broker.account`) | claude | DONE (PR #39, merged ee66aaf; P3 F-025 → #52) | T0 |
| T3b | #15–#29 | Phase 2–4 interface spec (backtest, risk, execution, state, reconcile, engine, dashboard) | claude | DONE (PR #62, merged 0882031; Codex PASS ae80ac3) | T0 |
| T4 | #14 | Signal report + CLI (`scan`, `data fetch/status`, `account`, `config check`) | claude | DONE (PR #50, merged ce2490d) | T1, T2, T3 |
| T5 | #15 | Event-driven backtest engine: next-open fills, costs, slippage | codex | DONE (PR #75, merged 1d147df; ADR-008 in #80, merged 03ce186; P3s #79) | T1, T2 |
| T6 | #16 | Performance metrics, SMH/QQQ benchmark comparison | codex | DONE (PR #81, merged 700d90d; Claude PASS c613da9; P3 F-154 → #79) | T5 |
| T7 | #17 | Walk-forward + parameter sensitivity | claude | READY | T5, T6 |
| T8 | #19 | Deterministic independent risk manager (exposure incl. pending, halts) | claude | DONE (PR #69, merged f79a496; Codex PASS a87d30c) | T2 |
| T9 | #21 | Paper order lifecycle: idempotent client IDs, partial fills, brackets | claude | IN PROGRESS (claude): part 1 DONE (#78); part 2 gateway/client IDs/OrderManager `feature/21-order-lifecycle` | T3, T8 |
| T10 | #22 | Broker reconciliation + restart recovery | codex | READY | T9 |
| T11 | #23 | Position management, stop/target, time exits | claude | READY | T9 |
| T12 | #24 | Exchange-calendar scheduler / trading loop | codex | READY | T9, T10, T11 |
| T13 | #25 | Streamlit dashboard (dark theme) | codex | DONE (#82/#100/#109; daily Charts #117 and 5-minute Charts #120 merged) | T4 |
| T14 | #26 | Discord notifications | codex | READY | T12 |
| T15 | #27 | Docker / compose, health checks, graceful shutdown | claude | READY | T12 |
| T16 | #29 | Security: secret handling, redaction, prompt-injection tests | codex | IN PROGRESS (codex): core and redaction follow-ups #74/#101 merged; §13 e2e test open | T4 |
| T17 | #18 | Corporate actions tracking | codex | DONE (core PR #77 merged 5178a1e; #18 stays open for coverage and integration) | T1 |
| A37 | #37 | Audit: reject missing runtime configuration sections | codex | DONE (PR #38) | T0 |
| A40 | #40 | Harden shared paper endpoint guard against disguised URL objects | codex | DONE (PR #41, merged dfece2a) | T0 |
| T42 | #42 | Offline fixture CSV-to-Parquet ingestion pipeline | codex | DONE (PR #45, merged 9aa0e0c) | T0 |
| T46 | #46 | Offline point-in-time split normalization core (subtask of #18) | codex | DONE (PR #61, merged ae01934; P3 F-041 → #68) | T0 |
| T47 | #47 | Project status dashboard (separate from trading UI) | codex | DONE (PR #65, merged 068e01f; CI job split to restricted #66, owner approval) | T0 |
| A63 | #63 | Reject unverified retained adjusted sessions (F-036) | codex | DONE (PR #64, merged 0fe06f3; P3 F-050/F-051 → #68) | T1 |
| A68 | #68 | Split precision and adjusted-history recovery follow-ups | codex | DONE (PR #70, merged 00be4fa) | T46, T1 |
| T71 | #71 | Backtest cost/config and shared exit primitives (subtask #15) | codex subagent | IN PROGRESS (codex subagent, WIP): isolated primitives preserved for lead reuse; overlaps lead-owned T5 | T3b |
| A74 | #74 | Security follow-ups F-070..F-076 | codex | DONE (PR #76 merged c7698dc; F-102 forms continue in #98) | T16 |
| A79 | #79 | Backtest review follow-ups F-090..F-095 and F-154 | codex | DONE (owner-approved PR #97 merged cac61bb) | T5, T6 |
| A98 | #98 | Remaining free-text credential forms (F-102) | codex | DONE (PR #101 merged 32128fd; 2150 passed/two skips) | T16 |
| A103 | #103 | Equity-chart observation/session handling | codex | DONE (superseded by dashboard redesign #108 and price Charts #117; #105 closed) | T13 |
| A122 | #122 | Dashboard status truthfulness and current strategy label (F-177/F-178) | codex | REVIEW (PR #125 at 52d77d2; 2209 passed/two skips; Claude review requested) | T13 |
| I128 | #128 | $5k risk profile, decoupled defaults, account models in the spec | claude | REVIEW (PR #129; restricted, owner approval) | T8 |
| I131 | #131 | Intraday backtest: intraday_v1 at $5k, 3 account models x 3 slippages | claude | REVIEW (PR #130; code-owned, owner approval; stacked on #129) | I128, #118 |
| I133 | #133 | dip_v1: pre-registered swing dip-buying (spec #134 merged; backtest PR #135) | claude | REVIEW (PR #135; code-owned, owner approval; stacked on #129/#130) | I128, I131 |
| I137 | #137 | intraday_dip_v1: same-day dip-buying, main strategy (spec #138 merged; backtest PR #139) | claude | REVIEW (PR #139: development FAIL, holdout unused; code-owned, owner approval) | I133 |
| A146 | #146 | F-179: cap intraday fill quantity by actual entry-to-stop risk | codex | REVIEW (PR #152 at 53e409d; Claude review requested; 2257 passed/two skips) | I131 |
| H1 | #20 | Verified earnings calendar source (owner chose Finnhub primary + Alpha Vantage cross-check) | claude | DONE (#88; #115 isolates Finnhub parse failure to TSM; R16 still blocks uncovered symbols and MRVL/MU date disagreements) | T8 |
| H2 | — | Alpaca PAPER API keys in local `.env` for real-data runs | human | BLOCKED (owner) | — |
| H3 | — | GitHub Actions billing/spending limit: since ~12:00 UTC 2026-11-13 no CI job starts, so nothing can merge | human | DONE (repo made public 2026-11-13; #81 CI re-run green 16:45 UTC) | — |

## Codex queue (set by the lead; work top to bottom, one item at a time)
Codex: start here every session. When an item is done, post on the PR or issue, add a log line, and
delete the item from this list in the same `BOARD.md` commit. If an item is blocked, say why in the
log and move to the next one. Work in your own worktree under `../semi-edge.worktrees/`, not in the
`.t3` checkout and not in a Claude worktree. Never push to a Claude branch. Report findings on the
PR instead. **Next finding ID: F-179** (F-160..F-164 used in the Claude review of #82; F-060 is your #67 security fix; F-061..F-063 were used in your #69 review).

When Codex is back: exact-head review of #139 (intraday_dip_v1) and #135 (dip_v1 backtest, Claude self-reviewed). #129 exact-head PASS confirmed at 638ce24; #130 remains FAIL F-179 at 901936c. Fix #152 at 53e409d awaits Claude review and integration before #130 re-review.

#100/#108/#109/#117/#120 dashboard work is merged. #101 (redaction) and #115 (TSM Finnhub isolation) are merged; #103/#105 were closed as superseded. #125 fixes F-177/F-178 and awaits Claude review.

## Review queue
| PR | Task | Author | Reviewer | Verdict |
|---|---|---|---|---|
| #125 | #122 | codex | claude | F-177/F-178 addressed; exact head 52d77d2, 2209 passed/two skips; review requested |
| #97 | #79 | codex | claude + owner | DONE owner approved, merged cac61bb; Claude PASS and CI green |

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

- 2026-11-13 03:50 UTC · claude · T4 → draft PR #50 (CLI signal report, closes #14). Stacked on
  #36/#39/#48 via merge commits; only `45ab740` is new. Validation: 930 passed, 1 skipped; ruff/mypy clean.
  Codex review requested. Claude is reviewing Codex PR #49 (dashboard). Still waiting for Codex
  reviews of #36 (`492b75e..b2c43f0`), #39 (`da04e38`), #48 and #50. Merge order once they PASS: #36, #39,
  #48, then rebase #50.

- 2026-11-13 02:19 UTC ? codex ? PR #45 fixes ready at 6d331d3, local branch feature/42-ingestion-review-fixes, worktree ../semi-edge.worktrees/codex-ingestion-fixes; fast-forward pushed to existing feature/42-offline-ingestion. F-013/F-014/F-015 recorded/fixed and Claude re-review requested on PR. 294 tests pass; Ruff check/format, mypy, Bandit pass; non-integration branch coverage 84.74%. Original .t3 and Claude worktrees preserved. Asked lead next review priority on issue #42.

- 2026-11-13 04:20 UTC · claude · Reviewed Codex #49: FAIL. F-016 (P2, rule 4: the refresh script drops unknown
  rows and maps unknown statuses to READY, then exits 0) and F-017 (P2: hard-coded or stale status claims that
  contradict the board), plus F-018..F-023 (P3); details are in the PR comment. #50: Claude pre-screen found one P2 and four
  P3s, all fixed with regressions (936 passed). Next finding ID: F-024. Docs PR #51 (.t3 migration
  status) also awaits Codex review.

- 2026-11-13 02:27 UTC ? codex ? Continuous review wave completed: #36 b2c43f0 FAIL with new F-024/P1 (out-of-order fetched_at can splice adjusted bases; offline closes [10,100], 357 tests pass). #39 da04e38 PASS (546 tests, 1 opt-in smoke skipped); nonblocking F-025/P3 timestamp-offset validation tracked #52. #48 38b7865 FAIL: F-026/P2 eligible infinite target from validated finite config; F-027/P3 oversized sizing integer overflow; 390 tests pass. All three have structured PR comments and Ruff/type/format evidence. Lead retains merge order; no unsafe merges. Next fixing own dashboard #49 F-016..F-023 while Claude handles data/signals findings.

- 2026-11-13 04:55 UTC · claude · Merged #39 (`ee66aaf`, Codex PASS). Fixed F-024 on #36 (`2c396ef`) and
  F-026/F-027 on #48 (`92e9b48`); each regression fails on the reviewed head. Added a "Codex queue" section
  above the review queue: Codex, start there. Next finding ID F-028.

- 2026-11-13 02:36 UTC ? codex ? Continuing with own #49 dashboard review fixes F-016..F-023 in ../semi-edge.worktrees/codex-dashboard-fixes (feature/47-dashboard-review-fixes), preserving prior Site/engine worktrees. Review queue synchronized to posted #36 FAIL / #39 PASS / #48 FAIL results. Dashboard now derives current phase/owner blockers/review text from source; adding fail-closed parser and browser-state regressions plus CI coverage.

- 2026-11-13 05:20 UTC · claude · Claude re-review of #45 at `6d331d3`: PASS. Merged #45 (`9aa0e0c`).
  Phase 0 audit: #2 meets its criteria. #3 is a code-complete disabled prototype, blocked on #4–#6. #1 has
  docs drift, fixed in PR #53 (restricted, needs the owner). Governance tooling defects are filed as #54. Next
  finding ID F-028.

- 2026-11-13 05:45 UTC · claude · Codex re-review of #36 at `2c396ef`: PASS. Merged #36 (`c424d48`). The data layer is
  on `main`. Opened #55 (F-025, closes #52). Claude is re-reviewing Codex's #49 fixes (`4a8bd9d`). Merge order next:
  #48 once Codex PASSes, then rebase #50 onto `main` and mark it ready.

- 2026-11-13 06:05 UTC · claude · Claude re-review of #49 at `4a8bd9d`: PASS, with P3 F-028 (`?` mojibake in the banner) and
  P3 F-029 (hard-coded owner setup text). It needs a rebase (`AUDIT_REPORT.md` conflict, so no CI yet) and owner
  approval (`ci.yml`). Next finding ID F-030.

- 2026-11-13 02:52 UTC ? codex ? Queue handoff: #36 2c396ef PASS (362 tests), now merged; #48 92e9b48 FAIL same F-026 undefined-ATR bypass (394 tests pass); #50 c1f8cf8 FAIL F-030/P2 account constructor exception sanitization (936 passed, 1 opt-in smoke skipped). #51 docs and #55 F-025 PASS; #53 source PASS, owner approval remains. Finding collision corrected on PR #50: F-028/F-029 belong to dashboard, CLI is F-030; next F-031. #49 rebased/follow-ups at 835d80c, 785 passed/1 skipped plus DOM tests and lint/types; Claude re-review requested. Site still old version because Sites Git returns sites_git_client_upgrade_required; no new deployment claimed. Moving to #46 per lead queue, all other worktrees preserved.

- 2026-11-13 02:58 UTC ? codex ? Delivered #46 as PR #61, feature/46-split-core at a225f7c in ../semi-edge.worktrees/codex-split-core. Pure RAW split normalization with effective/knowledge cutoffs, duplicate/provenance/numerical guards, unchanged inputs, 35 hand-written regression cases. Full 806 passed / 1 opt-in smoke skipped; non-integration coverage 93.58%, Ruff check/format, mypy and Bandit pass. Claude review requested. No vendor/broker/portfolio integration or event-coverage claim. Next queue item T5 waits for #48 and T3b interface review; starting independent spec inspection meanwhile.

- 2026-11-13 06:40 UTC · claude · Merged #51 (`067ae21`) and #55 (`9155740`, closes #52), both Codex PASS. Fixed the F-026
  remainder on #48 (`44d9624`) and F-030 on #50 (`c222b88`); the #50 stack is refreshed by merges, no force-push.
  Opened #62 (Phase 2–4 spec, aligned with the Phase 1 code; ADR-005..007). Claude is reviewing #61 and re-checking #49.
  Next finding ID F-031.

- 2026-11-13 06:55 UTC · claude · Handoff (usage limit). Claude re-check of #49 at `835d80c`: PASS, CI green, MERGEABLE; it only
  needs owner approval. A Claude review of #61 may still post on its own. If it hasn't, it is the next Claude task.
  Open Claude items: merge #48 on Codex PASS, then merge `main` into #50 and mark it ready; Dependabot #56–#60 have green CI
  (uv.lock only; #43 is superseded by #56) and are unreviewed. Branches: signals `44d9624`, CLI `d3f12ba`, spec `4f8eb8d`
  (`feature/15-phase2-4-interfaces`; the old `feature/15-phase2-4-spec` is superseded).

- 2026-11-13 03:08 UTC ? codex ? Bounded Claude Code source review of #61 a225f7c returned FAIL: F-031/P2 consumed iterator, F-032/P2 unresolved effective split basis, F-033..F-035/P3 provenance/prefix/test gaps. Reproduced four failures against old source and fixed all at 28dd940; 39 focused / 810 full tests pass, 1 opt-in smoke skipped, Ruff/types/Bandit clean. Re-review invocation hit Claude five-hour limit (reported reset 12:20am America/Chicago); no approval/merge claimed. Next finding F-036. Supplemental Codex read-only data audit running; cannot substitute for Claude review. Backtest contract questions posted on #15; implementation still waits on #48 and T3b. Browser layout check blocked because browser access host unavailable; hosted Site still old because Sites Git client unavailable.

- 2026-11-13 03:12 UTC ? codex ? Re-reviewed Claude #48 44d9624 PASS (953 passed, one opt-in smoke skipped) and #50 d3f12ba PASS CLI scope (1030 passed, one skipped), Ruff/type/format checks clean. Squash-merged #48 with exact-head match and all 16 checks green, following the lead handoff and owner autonomous-merge authorization. #50 still awaits the lead-requested final main refresh; never pushed to a Claude branch. Supplementary data audit reproduced F-036/P2 (opposite split events leave a retained gap on a stale basis despite matching anchors); claimed #63 for fail-closed fix, new own worktree. #62 review continues. Next F-037.

- 2026-11-13 03:24 UTC ? codex ? Durable handoff: reviewed and squash-merged Dependabot #56..#60 after exact-head PASS and 16 green checks each; individually tested old heads (771/1 skipped), proposed merges with 0c4794b (953/1), and all five together (953/1); locked sync/Ruff/types passed. Local review merges aborted; other agents' worktrees preserved. #63 delivered as PR #64, now rebased 51852d4 (954/1) after dependency merges; #61 rebased 0f328ea (992/1), both await Claude review. #62 FAIL F-037..F-040 posted with reproductions and required acceptance cases; T5 waits on corrected contract. #50 current d3f12ba PASS but Claude's final refresh step remains. No pushes to Claude branches, no restricted merges, no broker requests or deployment. Next F-041. No scheduled/background worker is configured; Claude CLI reported quota reset 12:20am Central.

- 2026-11-13 06:30 UTC · claude · Resumed after the usage reset. Owner (overnight brief): advance Phase 0/1 and the dashboard,
  with the lead in charge of Codex. When Codex is not running interactively, the lead runs Codex reviews through
  `codex exec --sandbox read-only` and posts them labelled as such; they still count as the cross-provider review.
  #50 was refreshed onto `main` (CLI scope only); Codex confirmed PASS at `91096e0`. Merged #50 (`ce2490d`). **Phase 1 (#9–#14) is complete
  on `main`.** In progress: Claude spec fixes F-037..F-040 for #62, Claude re-review of #61 (IDs F-041..F-049) and review of
  #64 (IDs F-050..F-059).

- 2026-11-13 06:27 UTC ? codex ? Resumed with owner instruction. #50 merged ce2490d by lead; claimed READY T16/#29 in feature/29-security-boundaries. Implement shared redaction/untrusted-text primitives and offline injection tests against available logging/config modules; no invented engine/state integration. #61/#64 exact-head review requests sent to Claude; #62 findings remain queued. Existing worktrees preserved.

- 2026-11-13 06:40 UTC ? codex ? Delivered #29 boundary core as PR #67, feature/29-security-boundaries at 866e145, own ../semi-edge.worktrees/codex-security-boundaries. F-060/P2 reproduced/fixed unknown credential-labelled JSON log extras escaping redaction; old regression failed, now valid JSON stays redacted. Added strict deep JSON redaction, inert normalized/bounded UntrustedText and 29 safety cases/fixtures plus existing logging/temporary SQLite harness. Full post-rebase1073 passed/1 opt-in smoke skipped, focused35 passed, Ruff/format/types/Bandit pass. Rebase preserved both audit sections. Claude exact-head review requested; #29 remains open for future engine/outbox/dashboard consumers. Saw Claude PASS on #61 and #64; lead owns merges/follow-up allocation. NextF061; no broker access, no pushes to Claude branches.

- 2026-11-13 07:40 UTC · claude · Merged #64 (`0fe06f3`) and #61 (`ae01934`), both Claude PASS. The lead merged `main` into them (audit
  keep-both) because `codex exec` cannot write git metadata on Windows. Dashboard: #65 (Codex's #49 content minus the
  `ci.yml` job, Codex-reviewed) merged as `068e01f` and closes #47. The CI job is restricted PR #66 (owner). #62: Codex re-review at
  `de9cd14` found F-043/F-044 (P2) and F-045 (P3); fixed at `481f8b5`, re-review running. P3 follow-ups are in #68. Next: T8.

- 2026-11-13 08:20 UTC · claude · #62: Codex re-review PASS at `481f8b5`; P3 F-046 fixed at `ae80ac3` and being confirmed. T8 risk manager
  opened as #69 (76 safety tests; R16 rejects every PAPER entry until #20). Codex reviews of both are running via `codex exec`.

- 2026-11-13 06:46 UTC ? codex ? #62 current481f8b5 re-reviewed PASS; report posted, no runtime readiness claim. #67 rebased13d12dd after data merges,1113 passed/1 skipped plus Ruff/types/format; Claude review requested. Claimed next lead-queue item #68 (F-041/F-050/F-051) in own feature/68-data-followups, sibling codex-data-followups. Preserve all other worktrees; T5 follows the lead order.

- 2026-11-13 09:00 UTC · claude · Merged #62 (Phase 2–4 spec, `0882031`; Codex PASS after F-037..F-046) and #69 (T8 risk manager,
  `f79a496`; Codex FAIL F-061/F-062 P2 and F-063 P3, all fixed, then Codex PASS with 90 tests run). Codex is not running
  interactively, so the lead is driving it through `codex exec`. Codex is implementing T5 (#15) in `../semi-edge.worktrees/codex-backtest-15` and
  #68 P3s in `codex-p3-68`. The sandbox cannot write git metadata, so the lead commits its work labelled `Implemented-by: codex` and a
  Claude reviewer reviews it. Leftover: `../semi-edge.worktrees/codex-run-69/.review-pytest-tmp` is permission-locked (harmless).

- 2026-11-13 06:52 UTC ? codex ? Delivered lead-queue #68 as PR #70 at8cef298, own feature/68-data-followups. Fraction composition applies each row once; inverse-chain exact equality and events=None regressions fail on old source/pass now; numeric refusal guards preserved. Added manual no-delete cache recovery guide, tightened F-051 error matches, marked initial counts historical. Full1088 passed/1 skipped; split43 cases; Ruff/format/mypy/Bandit clean. Claude exact-head review requested. #62 is merged; next T5 can use the resolved contract, subject to lead review priorities.

- 2026-11-13 06:56 UTC ? codex ? Started T5/#15 against merged #62. Own feature/15-backtest-engine in codex-backtest-engine. Delegated bounded #71 primitives to Codex agent in a separate feature/71-backtest-primitives worktree; public helper signatures coordinated, root owns shared risk types/proposals and engine/results. Claude retains T8. No fake/permissive gate in shipped CLI; test gates only in fixtures. No market performance result or live/credential access.

- 2026-11-13 09:30 UTC · claude · Closed duplicate #72; Codex's own #70 covers #68 and Claude is reviewing it along with #67. **T5 is taken** by a
  lead-run `codex exec` in `codex-backtest-15`, so interactive Codex should take T17 (#18) next. Corrected the finding-ID register: next is F-064.

- 2026-11-13 07:01 UTC ? codex ? Coordination correction: T5 branch already belongs to lead-run Codex; creation failed safely, no edits to its worktree. Following lead 09:30 handoff, interactive Codex now claims T17/#18 in new feature/18-corporate-actions. #71 agent preserves isolated primitives for possible lead reuse, no duplicate PR. SDK inspection shows corporate-action models lack an announcement timestamp; will not invent known_at from fetched_at. Building typed core/store/consistency with explicit verified knowledge requirements and offline tests; vendor support limits will be documented.

- 2026-11-13 10:40 UTC · claude · Merged #70 (#68, `00be4fa`) and #67 (#29 security core, `f0b093d`), both Claude PASS. Follow-ups F-070 (P2) through F-076 are in
  #74 for Codex. #73 (#54 governance tooling): Codex FAIL F-077, then F-078/F-079 (P2 reviewer-config bypasses via a truncated diff,
  renames and quoted paths), all fixed, then Codex PASS at `c24c43b`. It is restricted, so it waits for the owner. T5 backtest delivered by `codex exec` as #75;
  Claude review running. Next finding ID F-080 (F-090..F-099 reserved for the #75 review).

- 2026-11-13 07:18 UTC ? codex ? #74 security follow-ups delegated to isolated Codex agent feature/74-security-followups while root handles T17. Agent reports1283 full tests/1 skipped and65 new security regressions; no approval claimed, PR/Claude review pending. Root T17 typed ledger/raw read-only adapter has48 focused tests passing; exact counts, atomic ledger, explicit knowledge resolver and bounded pagination. Corporate adjusted-split contract concern on #18 is proposed F077 (not F064 already used on #70); not integrated into scanner/engine. #71 coordination WIP83798f0 is pushed for lead reuse, no duplicate PR.

- 2026-11-13 07:32 UTC · codex · Delivered T17 core PR #77 at96d6c65: exact corporate-action DTO/ledger, bounded read-only data adapter requiring separate announcement evidence, adjustment consistency checker;66 safety cases,1273 full passed/1 opt-in smoke skipped, Ruff/types/Bandit pass. No consumer activation or verified coverage claim; #18 remains open. Proposed policy clarification has no finding ID (F077..79 belong to governance). #76 refreshed288ed94 fixes confirmed backslash-suffix leak;1292 passed/1 skipped, nine regressions (eight fail on prior head). Both exact-head Claude reviews requested. T5 and other worktrees untouched; #71 remains preserved coordination WIP.

- 2026-11-13 11:40 UTC · claude · Claude review of Codex #77 (#18 corporate actions) at `96d6c65`: FAIL. Blocking P2s F-110 and F-111, plus
  P3s F-112 and F-113; details are on the PR. Claude reviews of #75 (T5) and #76 (#74 security follow-ups) are running, and the state store (T9 part 1) is being
  finished. The finding-ID register is now F-120 (F-080..F-119 are reserved for reviews this wave).

- 2026-11-13 12:20 UTC · claude · Merged #75 (T5 backtest, `1d147df`; Claude PASS; P3s are in #79). Claude reviews: #76 FAIL (F-100 P2, F-101..F-104
  P3), #77 FAIL (F-110/F-111 P2); both are back with Codex. Opened #78 (state store, T9 part 1) and #80 (F-096/ADR-008: the backtest entry
  is invalidated when the open is at or below the stop, decided before any results); Codex reviews of both are running via `codex exec`.

- 2026-11-13 11:35 UTC · claude · Merged #80 (ADR-008, `03ce186`; Codex FAIL F-130/F-131 parity-spec gaps, fixed, then PASS). The lead claimed T6 (#16) for a
  `codex exec` run. #78 (state store) is being fixed for Codex's F-120..F-123.

- 2026-11-13 11:55 UTC · claude · Merged #78 (T9 part 1, `semiedge.state`, `5a378da`). Codex review chain: F-120..F-123 P2, then F-140..F-144 recovery-spacing
  precision bypasses, all fixed, then Codex PASS. Added bandit `nosec B608` on constant-column SQL to fix CI `sast`. #21 reopened for part 2. T6
  delivered by `codex exec` as #81; Claude FAIL F-150 (P2: period returns drop the first session), and `codex exec` is fixing it. Next finding ID F-160.

- 2026-11-13 12:05 UTC · claude · **CI is down:** every GitHub Actions job on #81 failed to start (account payment or spending limit). Owner item H3. No merges are
  possible until it is fixed (ADR-004 needs green CI). #81 has Claude re-review PASS at `c613da9` and is held. Next finding ID F-160.

- 2026-11-13 12:15 UTC · claude · Overnight report emailed to the owner and posted to Discord. Owner asks: H3 (Actions billing), merge restricted #73/#53/#66,
  #20 earnings source, and the #1 criterion decision. Merges are paused until CI runs again.

- 2026-11-13 12:20 UTC · claude · While CI is blocked (H3), work continues on unblocked tasks with local validation as the gate. Claude: T9 part 2 (#21). `codex exec`:
  T13 (#25) Streamlit trading dashboard (read-only state, manual-halt request only). Nothing merges until CI is back.

- 2026-11-13 16:55 UTC · claude · H3 resolved: the owner made the repo public, and #81's failed CI re-ran green. Fixed board statuses for T16 and T71, which made `project-status/scripts/refresh-snapshot.py` refuse to refresh (status must start with READY, IN PROGRESS, REVIEW, DONE or BLOCKED followed by a space). Owner decision: AGENTS.md is canonical; #1's criterion was amended and PR #53 now closes #1.

- 2026-11-13 17:30 UTC · claude · Lead resumed on the owner's second PC (no `../semi-edge.worktrees/` or Codex CLI here; worktrees on the other PC are untouched). Merged #81 (T6, `700d90d`; Claude PASS, CI green after the public-repo rerun). Reran #82's billing-era CI. Owner decisions: #20 source is Finnhub (primary) + Alpha Vantage (cross-check), keys `FINNHUB_API_KEY`/`ALPHAVANTAGE_API_KEY`, R16 unchanged; no Yahoo data. Claimed #20 (H1 row), #85 (cut Actions usage; restricted, folds in #66) and #86 (`docs/RUNBOOK.md`). Codex reviews of these need Codex run by the owner (no CLI here).

- 2026-11-13 18:30 UTC · claude · Opened #87 (#85 CI: fail-closed docs-only classifier, path filters, uv/npm caches, PR-only cancel, monthly grouped Dependabot, extras audited; supersedes and closed #66), #88 (#20 earnings: Finnhub + Alpha Vantage cross-check, overrides, daily cache; R16 unchanged; 1696 passed locally) and #89 (#86 RUNBOOK). Merged `main` into #53 and #73 (conflict-only; owner approval remains). Claude review of #82: FAIL, F-160 (P2, held sessions off by one vs ADR-005) plus P3 F-161..F-164. Open question for the owner: `config/app.yaml` uses `daily_feed: sip`, but the owner note says IEX for now. Not changed, because the feed feeds the volume conditions.

- 2026-11-13 19:00 UTC · claude · Owner approved and merged #73 (#54, `110cd83`) and #53 (#1, `b9ded4c`). Owner approved #87; it merges once Codex passes it. Owner still to do: Codex reviews of #87/#88/#89, #4–#6, and `.env` keys (H2).

- 2026-11-13 19:50 UTC · claude · Board correction: the owner merged #87 (#85) at 18:09 UTC (`56cd3a8`), before the 18:17 board note that said it was pending; Codex review of it is now post-merge. Claude review of Dependabot #90 (alpaca-py 0.45.0, drops pytz): PASS, paper `TradingClient`/`BaseURL.TRADING_PAPER` surface unchanged; merged `50300fa`. #5 check: agents run as `Ferrisfy` (admin false, push true), `human_maintainers` is only `Ferrsir`; `agent_identities` still empty and `main` has 0 rulesets (#4), so merge rules are process-only.

- 2026-11-13 20:20 UTC · claude · Opened #91 (#5: `agent_identities: ["Ferrisfy"]`, `load_policy` rejects human/agent overlap and malformed lists, setup report re-verified) and #92 (#4: `docs/governance/main-ruleset.json`, fail-closed test, runbook). Both are restricted and need a Codex review plus owner approval. #92 has two owner decisions: how board updates work once PRs are required (AGENTS.md is code-owned), and strict up-to-date checks. After #92 merges, the owner imports the ruleset, then #7 refusal tests.

- 2026-11-13 20:55 UTC · claude · Owner delegated the two #92 decisions. Chosen: board, queues and log move to non-code-owned `BOARD.md` (#93, PR #94; must merge before the ruleset is applied), and required checks are not strict (`28fd2b9`). Order for the owner: Codex reviews, approve and merge #94, #91, #92, import the ruleset, then #7 refusal tests.

- 2026-11-13 21:00 UTC · claude · Board, queues and log moved verbatim from `AGENTS.md` to `BOARD.md` (#93), so board updates need no owner approval once the #4 ruleset requires PRs. Codex: write log lines and claims here from now on; an unmerged `AGENTS.md` log append on another branch should be moved here when rebasing.

- 2026-11-13 20:54 UTC · codex · Resumed lead queue: exact-head reviews posted, #91/#89/#87 PASS; #92 FAIL F-165 (documented approval-setting contradiction, no activation); #88 FAIL F-166/F-167 (offline token-redirect and malformed-cache reproductions), full1696 passed/1 skipped plus lint/types. #82 claimed in own codex-dashboard-fixes-82: three holding-count regressions fail old f5a99d0 (0/1/9 vs1/2/10), shared-helper fix and docs cleanup underway;40 dashboard tests including10 Streamlit AppTests pass. Main snapshot and all other worktrees preserved. New rules/BOARD split read after #94 merged; next finding F-168.

- 2026-11-13 21:15 UTC · claude · #91, #92 and #94 merged by the owner. #92 merged with Codex FAIL F-165 open, so the ruleset is not imported yet; fix is #95 (regression fails on the old file). Merged #89 (RUNBOOK, Codex PASS c970fc7, `b027cc3`). Next Claude: fix #88 F-166 (P1) and F-167 (P2). Owner: approve and merge #95, then import the ruleset.

- 2026-11-13 21:25 UTC · claude · Owner decisions: `daily_feed: sip` stays (resolves the SIP vs IEX question, no change). Standing authorization: the lead merges any PR once it has a cross-provider PASS at its head and green CI; restricted PRs still need owner approval. To-do list and day report emailed to the owner (Discord not configured on this PC). Claude now fixing #88 F-166/F-167.

- 2026-11-13 21:40 UTC · claude · #88: F-166 (refuse all vendor redirects through a private opener) and F-167 (type-check every cache field, malformed means missing) fixed at `0b9a0e9`; 1763 passed, both bugs reproduced on `a7e8389`. Ran the #82 dashboard on a synthetic DB: it works; posted UI findings F-168..F-174 (P3) on #82 and screenshots to the owner's Discord. Next finding ID F-175.

- 2026-11-13 21:30 UTC · codex · Verified Ferrisfy connector identity and write/non-admin access. Published existing validated fixes through that identity, preserving original PR histories and exact tested Git trees: #82 873b589 (1632 passed/two skips, all ten Streamlit AppTests), #76 c529059 (1700/two), #77 ab3ff1a (1666/two); Ruff check/format, mypy and Bandit pass. Claude exact-head re-review requested on all three. #94 post-merge PASS (original board preserved; 15 snapshot regressions); #95 PASS 96f8fa6 (130 governance/one skip; owner approval/activation/#7 remain); #88 PASS 0b9a0e9 after F-166/F-167 fixes (1763/one opt-in skip, source/R16 104, Ruff/types pass). No broker/vendor/credential access, merge, ruleset activation or deployment. Main snapshot and other agents' work preserved. UI P3 F-168..F-174 remain follow-ups for lead allocation; next finding F-175.

- 2026-11-13 21:34 UTC · codex · Owner asks to keep progressing and take unblocked work. Claude PASS on #82/#76 observed, #76 merged; lead owns dashboard UI follow-ups and #77 review. Claimed Codex-owned #79 (F-090..F-095, F-154) in isolated feature/79-backtest-followups, codex-backtest-followups-79. F-096 remains the lead's ADR-008 spec scope; no results tuning or risk/strategy changes.

- 2026-11-13 22:03 UTC · codex · Delivered #79 as PR #97 at 6a4f802: F-090..F-095/F-154, 2045 passed/two skips with dashboard extra; Ruff/format/mypy74/Bandit pass;14 old-source regression failures reproduced. Claude review requested; no strategy/risk change. All prior #82/#76/#77 fixes now merged by lead. Main ruleset is active; board claim #96 demonstrated board-only merge after green checks with no approval, and subsequent board changes use PRs. Next own task #98 addresses the explicitly deferred F-102 credential forms, separate isolated feature/98-redaction-forms worktree; dashboard UI remains Claude's. No broker/vendor/credential access or deployment.

- 2026-11-13 22:20 UTC · claude · Owner imported the `main` ruleset (active; matches `docs/governance/main-ruleset.json`); board changes now go through board-only PRs, and #96/#99 merged that way without approval. Merged after review: #88 (Codex PASS `0b9a0e9`), #76 and #77 (Claude PASS; #77 needed a conflict-only `AUDIT_REPORT.md` merge), #82 (Claude PASS; conflict-only merges for `cli.py` and `AUDIT_REPORT.md`). Opened #100 with the dashboard UI follow-ups; before and after screenshots posted to the owner's Discord. Next Claude: review #97.

- 2026-11-13 22:20 UTC · codex · #98 delivered as #101,2124 full passed/two skips,248 focused/Ruff/types/Bandit pass. Literal synthetic PEM fixture text triggered gitleaks across fetched branch history; fixed only fixture construction at cc5cb75 with exact lease, no scanner change; pinned/checksummed gitleaks8.30.1 scans162 remote commits clean. Retried #102 failed security jobs; all checks green, board-only #102 merged304844c. #100 extra-enabled2027/two suite passes but default locked install reproduces two fake-UI failures plus mypy missing Altair; superseding verdict FAIL F-176/P2 posted. F-175/P3 chart month/day collisions reproduced and tracked #103, own isolated feature/103-equity-chart; nextF177. #97 remains with Claude. No credentials/broker/vendor/production changes.

- 2026-11-13 22:27 UTC · codex · #97 Claude PASS at6a4f802 and CI green; owner approval remains for code-owned backtest path. #101 cc5cb75 CI now green after fixture-only PEM marker construction; no scanner/allowlist change. #100 remains author FAIL F-176 (two base locked fake-UI errors and mypy missing Altair); #103 F-175 prepared as dependent draft #105 d7f3f12, three old-source failures fixed, full2030/two with extra, focused51/Ruff/format/mypy75/Bandit pass. Draft waits on repaired #100 base and revalidation, not ready to merge. Main snapshot/other worktrees preserved; no broker/vendor/credentials, protection changes or deployment. NextF177.

- 2026-11-13 23:23 UTC · codex · Owner approved #97; confirmed merged cac61bb, no further owner approval pending for it. Refreshed own #101 to dd16c05 after that merge, preserving both conflicting audit sections; full2150/two, Ruff/format/mypy74 pass, Claude review requested at new head. #100 new12b1607 re-reviewed PASS: pure Vega-Lite spec removes optional Altair dependency and fixes core F-175; base full2018/12 skips, extra dashboard49 including ten AppTests, base/extra mypy75 and CI green. #105 remains draft and must be rebased/rescoped to avoid duplicating author's fix. Owner to-do list: confirm local PAPER/Finnhub/AlphaVantage setup if not already done; no additional approval currently requested. Agent priorities: #100 lead merge, #101 Claude review, #105 cleanup, finish #21 then #22/#23/#24; #17 is also unblocked. Governance setup report/#4 descriptions are stale about already-active ruleset; update status docs and complete remaining #3/#6/#7 work without claiming full enforcement proof. No credentials read or broker/vendor calls.

- 2026-11-14 00:00 UTC · claude · #100 merged (`3efbfbe`; F-176 fixed by a plain Vega-Lite spec, CI checked in a base install). Owner asked for a desk modelled on a reference video and approved escaped custom HTML plus an all-dark theme: #108, PR #109 (desk on Overview, injection tests, read-only `risk_decisions(session)`; the dashboard still never imports the writer). Screenshots in the owner's Discord. Owner kept all extensions in /doctor; auto mode is now the user default.

- 2026-11-14 01:30 UTC · claude · Owner decisions: (1) SEMI-EDGE is a day-trading system. `momentum_v1` (daily swing) is retired; the new strategy is `docs/specifications/intraday_v1.md` (ADR-010, #111, merged as #112 `b2d4066`): long-only on 5-minute bars, three setups (opening-range breakout to 11:30, prior-day-high breakout to 15:30, VWAP pullback 10:30-15:00), flat by 15:55, same 16 semis, unchanged risk limits. Written before any intraday data was examined. (2) Codex is out of quota, so Claude reviews its own PRs, labelled "Claude self-review", until the owner says Codex is back. #109 (desk) merged `cc74054` after self-review. Real-data runs with the owner's keys: paper account read (100k, flat), daily bars cached 2023-01-03..2026-11-13, swing backtest +3.4% (`semiedge backtest --source cache --start 2023-06-01 --end 2026-11-13`). Known issue: Finnhub returns `2330.TW` for TSM, which fails the whole Finnhub refresh and blocks all symbols in R16; fix next (TSM-only block). Next Claude tasks: 5-minute data fetch/cache, then the intraday backtest engine.
- 2026-11-14 02:47 UTC · codex · Closed #105 and #103 as superseded: #108 replaced the old Overview equity chart, while #117 delivers the current read-only daily price Charts page. No rebase is useful because it would restore the obsolete dashboard implementation. #101 remains with Claude for cross-provider review.
- 2026-11-14 02:55 UTC · codex · Reconciled board with main: #101 merged at 32128fd, #115 at d9a0a43 (per-symbol TSM isolation), and #117 at 5dea98b (read-only daily price Charts). Updated T13/T16/A98/H1 and removed the stale #101 review row. #103/#105 closure already recorded.
- 2026-11-14 03:06 UTC · codex · #109 post-merge safety review found F-177/P2 (RUNNING without a fresh engine heartbeat) and F-178/P3 (retired momentum_v1 desk branding). Opened #122, implemented fail-closed status plus neutral active-strategy wording in #125 at exact head 52d77d2. Rebased after #120/#124; full2209/two, Ruff/format, mypy78 and Bandit pass. Claude review requested; no trading/risk changes.
- 2026-11-14 04:40 UTC · claude · #130 (closes #131): `semiedge backtest-intraday` replays intraday_v1 at $5k for unrestricted, margin_pdt and cash accounts at 10/20/30 bps; code committed `901936c` before the first real-data run. Result (`semiedge backtest-intraday --start 2024-12-16 --end 2026-11-13 --equity 5000 --risk-profile 5k`): all nine runs lose about 5% and hit the 5% drawdown halt (unrestricted/cash within 3-5 weeks; margin_pdt later only because PDT throttles it). Expectancy -0.39R to -1.12R net, -0.01R to -0.18R with slippage backed out: no edge on this data. Nothing tuned (rule 3). #129 and #130 need owner approval. Next Claude: review Codex #125, then strategy-agnostic paper plumbing (T9 part 2).
- 2026-11-14 05:10 UTC · claude · Owner delegated the replacement for intraday_v1. ADR-012: `dip_v1`, swing dip-buying in semis held up to 3 sessions, overnight allowed; two signals pre-registered with 2016-2024 development and a 2025-2026 holdout (spec #134, merged). Daily cache extended to 2016-01-04. First development run void (SMCI frozen delisting bars); revision 1.1 (placeholder bars missing, planned-risk R) committed before the re-run. Development at 20 bps: S1 (RSI2<10) +0.008R over 865 trades vs baseline -0.039R, S1 selected (`4743980`). Holdout once: S1 +0.027R over 158 trades vs baseline -0.018R; $5k +$181 (+3.6%), maxDD -6.7%, halt 2026-10-14; SMH +99%. Pre-registered test PASS; the edge is small and its CI includes zero. Commands in `docs/results/dip_v1.md` on #135. Owner: approve #129, #130, #135.
- 2026-11-14 06:00 UTC · claude · Owner: the main strategy is same-day dip-buying, with no multi-day holds and no 50-day filter (ADR-013). `intraday_dip_v1` spec merged (#138); engine `42e72ad` committed before data. Development (`semiedge backtest-intraday-dip --period development --equity 5000 --risk-profile 5k`) at 20 bps: V1 -0.69R/861, V2 -0.79R/1461, baseline -0.88R; none qualifies, so FAIL, and the 2026 holdout is unused. Diagnostic at 0 bps: V1 -0.06R, V2 +0.01R (CIs include 0); stretched dips mostly hit the day's-low stop. Of the strategies tested so far, only `dip_v1` (swing) passed its pre-registered test. Owner decision needed on direction.
- 2026-11-14 06:40 UTC · claude · Handoff: owner moving from the laptop to the PC. gap_reversal_v1 (#141): spec PR #142 (open), engine on `feature/141-gap-reversal-backtest` at `e766111` (committed before any outcome); development run in progress on the laptop and to be repeated on the PC, then the selection commit, then one holdout. Approvals pending: #129, #130, #135, #139. Next: if gap_reversal_v1 fails, research and spec the next same-day idea; then T9 part 2/T11/T10/T12 so the dashboard shows paper trading. Codex queue: reviews of #135, #139 and the gap engine when Codex is available.

- 2026-11-14 06:08 UTC · codex · Owner-requested #129/#130 verification and #146 fix delivered: #129 exact-head 638ce24 PASS reaffirmed (165 config/sizing/risk tests, Ruff/format/mypy); #130 901936c FAIL F-179 reproduced with nine old-source failures across three accounts. Separate #152 at 53e409d adds actual-fill risk cap and ten regression/control cases; full2257 passed/two expected skips, intraday41, Ruff/format/mypy and CI Bandit pass. Claude exact-head review requested on #152; #130 remains FAIL until integration. No historical market rerun, strategy/ceiling change, broker request, owner approval or code merge claimed. Isolated codex-risk-fill-146 worktree preserved; main snapshot and Claude branches untouched.
