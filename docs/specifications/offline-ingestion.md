# Offline fixture ingestion (#42)

This pipeline prepares user-supplied historical CSV bars for offline research. It does not fetch market data, generate signals, place orders, or populate the vendor cache. It is independent of the pending Alpaca data and signal modules.

## Contract

`semiedge.data.csv_import.load_csv_bars(path, *, symbol, as_of)` returns a canonical single-symbol OHLCV frame. `parse_csv_bars(text, *, symbol, as_of)` validates an immutable text snapshot. The CSV must have exactly `session,open,high,low,close,volume` columns. Sessions are unique, ascending ISO dates on the XNYS exchange calendar. Prices must be finite and positive, volume finite and nonnegative, and high/low must enclose open/close. Invalid rows are rejected, including rows after the cutoff. Valid later rows are then excluded; an empty result is an error.

The caller explicitly supplies the latest completed session cutoff. The tool does not infer vendor availability, revision timing, split adjustments, earnings, or survivorship. A historical cutoff alone is not proof of point-in-time data correctness. Do not mix raw and adjusted CSVs or treat these inputs as verified vendor data.

`semiedge.data.fixture_ingest` writes a new Parquet file under a `fixture` or `fixtures` directory. It records symbol, source=`fixture`, completed-through cutoff, ingestion time, and SHA-256 of the exact CSV bytes parsed. Existing outputs are never overwritten. Validation occurs before output creation; serialization failures remove the partial output. Consumers must open only completed outputs after this command succeeds.

## Run without credentials

From the repository root, after `uv sync --locked --system-certs`:

```powershell
New-Item -ItemType Directory -Force data/fixtures
uv run python -m semiedge.data.fixture_ingest tests/fixtures/offline_bars.csv --symbol NVDA --as-of 2024-01-03 --output data/fixtures/NVDA-example.parquet
```

The sample has hand-written prices and is explicitly fixture data. No performance result or market claim follows from it. Re-running with the same output path fails; choose a new filename.

Python consumers can use `pandas.read_parquet` to inspect the snapshot. The provenance columns make it deliberately distinct from a strategy's bare OHLCV frame. This command does not automatically feed the cache, strategy, or trading engine.

## Verification

Run `pytest -q tests/unit/test_csv_import.py tests/integration/test_fixture_ingest.py`. Tests cover schema and numerical validity, dates, calendar sessions, cutoff handling, preserved provenance, sanitized CLI errors, exclusive creation, and cleanup on write failure. All tests use local fixtures.
