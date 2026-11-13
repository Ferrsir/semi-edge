# Decisions

ADR-001: fail closed until trusted enforcement exists. Local tests and AI comments cannot authorize merging.

ADR-002: separate research, merge authorization and deployment authorization. No model output can grant financial permissions.

ADR-003: use GitHub issues as durable backlog; no persistent worker is claimed without an available configured orchestrator.

ADR-004 (2026-10-30): the owner waived the GitHub branch-protection and trusted-review-issuer
requirements of ADR-001 for this private repository and delegated PR merging to the lead agent.
A PR may merge when CI is green and a cross-provider review (Claude ↔ Codex) recorded on the PR is
PASS with no open P0/P1. This does not relax ADR-002: no agent or model output can grant live
trading, raise risk ceilings, or add credentials. Paper-only enforcement stays in code.

ADR-005 (2026-11-13): `max_holding_sessions` counting. The entry session is held session 1; the
time exit is decided at the close of the session where `held_sessions >= max_holding_sessions`
and executed at the next open (`semiedge.strategy.exits`, phase2-4-interfaces.md §3.3). Made
before any backtest result exists. Rationale: `momentum_v1.md` defines the parameter but not its
counting, and backtest and paper must count identically. Changing it is a spec change.

ADR-006 (cited as D-006) (2026-11-13): the `momentum_v1` score weights (40/30/20/10) and
saturation points are heuristic priors with no statistical evidence behind them. They only order
eligible candidates and do not predict returns. Phase 2 sensitivity (phase2-4-interfaces.md §15)
must report their effect.

ADR-007 (cited as D-007) (2026-11-13): research data are split-adjusted price returns
(`adjustment: split`); dividends are excluded. Every backtest and research report carries the
label `PRICE RETURN: dividends excluded` (phase2-4-interfaces.md §3.6).

ADR-008 (2026-11-13): backtest entry invalidation also triggers when the raw open is at or below the
stop (`open_d ≤ stop`), not only when the slipped fill is. Without it, an adverse slippage could lift
the entry fill above the stop, and the position would later stop out at `stop · (1 − slip)`, which
is better than the open, so results would be optimistic. Made before any backtest result exists
(rule 3); found as F-096 in the review of #75. Strictly more conservative. Scope: backtest-only.
The paper engine has no trustworthy opening price at fill time, so §6.3 documents a parity exception
(F-130).

ADR-009 (2026-11-13): earnings data for R16 (#20). Owner decision: Finnhub's earnings calendar is
the primary source and Alpha Vantage's `EARNINGS_CALENDAR` is the cross-check. Keys come only from
`FINNHUB_API_KEY` and `ALPHAVANTAGE_API_KEY`. A date counts as verified when both sources are fresh
(`max_age_hours`), cover the query window and agree, or when a hand-checked entry in
`config/earnings_overrides.yaml` (investor-relations page, `verified_on` within
`override_max_age_days`) exists. Anything else (a missing key, a stale snapshot, a fetch error,
short coverage or a disagreement) is `verified=False`, which R16 rejects with
`earnings_data_unavailable`. A disagreement blocks only the affected symbol. An unusable source
blocks every symbol. Each source is refreshed at most once per UTC day (free-tier limits;
failures retry at most `max_attempts_per_day` times). R16, `earnings_blackout_sessions` and
`require_verified_earnings_data: true` are unchanged. Yahoo and other unofficial feeds are
excluded. The vendor calendar is not point-in-time, so backtests keep the labelled waiver.
Spec: `docs/specifications/earnings-calendar.md`.
