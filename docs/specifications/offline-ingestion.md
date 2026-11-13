# Offline fixture ingestion (#42)

This pipeline prepares user-supplied historical CSV bars for offline research. It does not fetch market data, generate signals, place orders, or populate the vendor cache. It is independent of the pending Alpaca data and signal modules.

## Contract

`semiedge.data.csv_import.load_csv_bars(path, *, symbol, as_of, now, settle_minutes=30)` returns a canonical single-symbol OHLCV frame. `parse_csv_bars(text, *, symbol, as_of, now, settle_minutes=30)` validates an immutable text snapshot. `now` is a required timezone-aware datetime; no hidden clock is read by the library. `as_of` must be an XNYS session whose close plus the settlement delay is at or before `now`. Settlement minutes must be an integer from 0 through 240. The CLI supplies the real UTC clock and the default 30-minute delay.

The CSV must have exactly `session,open,high,low,close,volume` columns. Sessions are unique, ascending ISO dates on the XNYS exchange calendar. All rows must already be closed and settled at `now`, including rows later than the cutoff. Prices must be finite and positive; volume must be a nonnegative integer exactly representable up to 2^53 shares (negative zero and fractional values are rejected). High/low must enclose open/close. Invalid rows are rejected before selecting rows through the cutoff; an empty result is an error. This importer deliberately supports dates from 1990 onward and does not project the current exchange rules into earlier history.

The completion check establishes exchange-session timing only, not vendor availability, revision timing, split adjustments, earnings, or survivorship. A historical cutoff alone is not proof of point-in-time data correctness. Do not mix raw and adjusted CSVs or treat these inputs as verified vendor data. Missing sessions between the first retained row and the cutoff are recorded in frame attributes; they are never filled. Downstream data-quality checks must still decide whether those gaps are acceptable.

`semiedge.data.fixture_ingest.ingest_fixture(..., now=...)` writes a new Parquet file directly inside a `fixture` or `fixtures` directory. Paths containing a `raw/bars/daily` layout or any `feed=`/`adjustment=` component are rejected even if a fixture ancestor exists. It records symbol, source=`fixture`, the validated cutoff, `last_session`, JSON-encoded `missing_sessions`, adjustment=`unknown`, injected ingestion time, and SHA-256 of the exact CSV bytes parsed. The cutoff does not claim the file contains every session through that date; consult the last-session and gap metadata. Existing outputs and output symlinks are refused. Validation occurs before output creation; serialization failures and interrupts remove the partial output. Consumers must open only completed outputs after this command succeeds; publication is not atomic for concurrent readers.

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
