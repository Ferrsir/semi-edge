# Decisions

ADR-001: fail closed until trusted enforcement exists. Local tests and AI comments cannot authorize merging.

ADR-002: separate research, merge authorization and deployment authorization. No model output can grant financial permissions.

ADR-003: use GitHub issues as durable backlog; no persistent worker is claimed without an available configured orchestrator.

ADR-004 (2026-10-30): the owner waived the GitHub branch-protection and trusted-review-issuer
requirements of ADR-001 for this private repository and delegated PR merging to the lead agent.
A PR may merge when CI is green and a cross-provider review (Claude ↔ Codex) recorded on the PR is
PASS with no open P0/P1. This does not relax ADR-002: no agent or model output can grant live
trading, raise risk ceilings, or add credentials. Paper-only enforcement stays in code.
