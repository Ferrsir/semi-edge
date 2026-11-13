# Architecture

Boundaries: data ingestion and validation, then deterministic features and signals, a backtest with
point-in-time inputs, risk sizing, a paper broker adapter, reconciliation, and dashboard and
operations. AI output is untrusted research input and cannot authorize orders. Governance scripts
use standard-library Python and do not import PR code in privileged jobs.

Which modules are implemented, in review or planned is tracked in the README status table and the
`AGENTS.md` board. Interface contracts live in `docs/specifications/` (`phase1-interfaces.md`, and
the Phase 2–4 draft on `feature/15-phase2-4-spec`).
