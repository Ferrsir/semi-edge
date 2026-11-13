# SEMI-EDGE

Semiconductor research and **paper-trading-only** platform under development. Live trading is
prohibited in code, config and policy.

## Status (2026-11-13)
"Tested" means covered by the offline suite in CI. Nothing is deployed.

| Area | Module | State |
|---|---|---|
| Configuration, paper-only safety guards | `semiedge.config`, `semiedge.safety` | implemented, tested (`main`) |
| Read-only paper account snapshot | `semiedge.broker.account` | implemented, tested (`main`, #39) |
| Data layer: calendar, sources, Parquet store, validation | `semiedge.data` | in review (#36) |
| Offline fixture ingestion | `semiedge.data.csv_import` | in review (#45) |
| Indicators, `momentum_v1`, position sizing | `semiedge.indicators`, `.strategy`, `.risk.sizing` | in review (#48) |
| Signal report + CLI | `semiedge.reporting`, `semiedge.cli` | draft PR (#50) |
| Backtest, risk manager, execution, reconciliation, scheduler, dashboard, notifications, deployment | — | planned (#15–#27) |

See [project brief](docs/PROJECT_BRIEF.md), [architecture](docs/ARCHITECTURE.md), the agent
rules and task board in [AGENTS.md](AGENTS.md), [engineering policy](docs/governance/ENGINEERING_POLICY.md),
[testing](docs/TESTING.md) and [setup status](docs/governance/ENGINEERING_SETUP_REPORT.md).
