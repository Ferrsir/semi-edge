# SEMI-EDGE

Semiconductor research and paper-trading platform under development. Available components include validated configuration, the stock universe, paper-only safety guards, offline diagnostics, and fixture-data ingestion. Trading and deployment are not implemented here.

See [project brief](docs/PROJECT_BRIEF.md), [architecture](docs/ARCHITECTURE.md), [engineering policy](docs/governance/ENGINEERING_POLICY.md), [testing](docs/TESTING.md), and [setup status](docs/governance/ENGINEERING_SETUP_REPORT.md).

## Offline data pipeline

Validate CSV daily bars and write a provenance-labeled Parquet snapshot without account credentials:

```powershell
uv sync --locked --system-certs
New-Item -ItemType Directory -Force data/fixtures
uv run python -m semiedge.data.fixture_ingest tests/fixtures/offline_bars.csv --symbol NVDA --as-of 2024-01-03 --output data/fixtures/NVDA-example.parquet
```

The example is hand-written fixture data, not actual NVDA prices. Existing output files are never overwritten. See the [input contract and limitations](docs/specifications/offline-ingestion.md) before importing your own CSVs. This command does not generate signals or authorize orders.
