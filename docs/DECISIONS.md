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
