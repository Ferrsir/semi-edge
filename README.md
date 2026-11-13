# SEMI-EDGE

Semiconductor research and **paper-trading-only** platform under development. Live trading is
prohibited in code, config and policy.

## Status (2026-11-13)
"Tested" means covered by the offline suite in CI. Nothing is deployed, and no result here is
evidence of a trading edge.

| Area | Module | State |
|---|---|---|
| Configuration, paper-only safety guards | `semiedge.config`, `semiedge.safety` | implemented, tested |
| Read-only paper account snapshot | `semiedge.broker.account` | implemented, tested (#39) |
| Data layer: calendar, sources, Parquet store, validation | `semiedge.data` | implemented, tested (#36) |
| Offline fixture ingestion, split normalization | `semiedge.data.csv_import`, `semiedge.data.splits` | implemented, tested (#45, #61) |
| Indicators, `momentum_v1`, position sizing | `semiedge.indicators`, `.strategy`, `.risk.sizing` | implemented, tested (#48) |
| Signal report + CLI | `semiedge.reporting`, `semiedge.cli` | implemented, tested (#50) |
| Backtest engine, performance metrics | `semiedge.backtest` | implemented, tested (#75, #81) |
| Risk manager, state store | `semiedge.risk.manager`, `semiedge.state` | implemented, tested (#69, #78) |
| Earnings calendar, corporate actions, trading dashboard | — | in progress or in review (#20, #77, #82) |
| Order lifecycle, reconciliation, scheduler, notifications, deployment | — | planned (#21–#27) |

See [project brief](docs/PROJECT_BRIEF.md), [architecture](docs/ARCHITECTURE.md), the agent
rules in [AGENTS.md](AGENTS.md), task board in [BOARD.md](BOARD.md), [engineering policy](docs/governance/ENGINEERING_POLICY.md),
[testing](docs/TESTING.md) and [setup status](docs/governance/ENGINEERING_SETUP_REPORT.md).

## Offline data pipeline

Validate CSV daily bars and write a provenance-labeled Parquet snapshot without account credentials:

```powershell
uv sync --locked --system-certs
New-Item -ItemType Directory -Force data/fixtures
uv run python -m semiedge.data.fixture_ingest tests/fixtures/offline_bars.csv --symbol NVDA --as-of 2024-01-03 --output data/fixtures/NVDA-example.parquet
```

The example is hand-written fixture data, not actual NVDA prices. Existing output files are never overwritten. See the [input contract and limitations](docs/specifications/offline-ingestion.md) before importing your own CSVs. This command does not generate signals or authorize orders.
