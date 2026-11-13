# Trading dashboard (#25)

The dashboard reads one local state database and its source-specific report directory.
It reads only `SEMIEDGE_DATA_DIR` from the environment (default `data`); it does not
load `.env`, application settings, credentials, broker clients or market-data clients.

Install with `uv sync --locked --extra dashboard`, then run
`uv run semiedge dashboard --source cache` or `--source synthetic`.
The launcher binds Streamlit to `127.0.0.1` and disables usage telemetry.
Alternatively use `streamlit run src/semiedge/dashboard/app.py -- --source synthetic`.
The checked-in Streamlit configuration supplies the dark theme and loopback address
when run from the repository root. Container packaging belongs to T15; its dashboard
stage must install the dashboard extra and share the state directory per spec §16.

## Data and controls

`cache` selects `state/semiedge.sqlite3`; `synthetic` selects
`synthetic/state/semiedge.sqlite3`. Database identity must match the selection.
No database is created or migrated by the UI. Missing/invalid state yields an explicit
error. The actual persisted mode and source appear above every page. A database
without a run has an explicit unavailable mode, rather than an assumed mode.
Synthetic databases always carry `SYNTHETIC DATA — NOT MARKET DATA`.

Overview, Signals, Positions, Trade history and Operations render persisted records
in plain-text tables. Signals select the newest run that actually contains evaluations,
irrespective of the job kind. Trade history is limited to the newest 500 orders/fills;
operations to 100 runs/reconciliations/errors. Protection uses all persisted protection
orders, showing their actual statuses and levels rather than assuming protection exists.
Held sessions, when equity snapshots exist, are exchange sessions since entry as of the
latest persisted equity session (entry session counts as zero); otherwise unavailable.

Heartbeat ages are calculated from persisted timestamps with an explicit UTC clock.
The current heartbeat schema has no interval column. If heartbeat `detail` contains
JSON metadata with a positive finite `interval_seconds`, ages strictly greater than
three intervals are marked stale and rendered with a red error indicator. Otherwise
freshness shows `unavailable: interval not recorded`. Engine integration must persist
that metadata to enable the spec's red threshold. Invalid interval metadata fails
closed. The UI never assumes an interval or reports an unverified healthy state.

Backtest JSON is read only from the selected source's `reports/backtests` directory.
Labels and reproducing command precede every report's values. Unknown schemas,
missing labels/commands, non-finite values, oversized files and source mismatches
produce explicit errors. No returns, benchmark values or demo account numbers are
generated. No file is executed. External text uses plain-text widgets and the shared
untrusted-text normalizer; there is no HTML or Markdown interpretation.

Operations requires an actor, reason and exact typed `HALT`. Its only mutation calls
`request_manual_halt`, which inserts one idempotent manual halt. This stops new entries;
exits and protective stops continue. There are no order controls or halt removal controls.

## Offline dependency status

`dashboard = ["streamlit>=1.40"]` is declared in `[project.optional-dependencies]`.
`uv lock --offline` was attempted; Streamlit is absent from the local cache, so resolution
failed and `uv.lock` remains unchanged. No network fetch was attempted. Before installation
in an environment with the package available, regenerate `uv.lock`, review that change,
then run `uv sync --locked --extra dashboard`.

For this offline implementation, validation uses `UV_FROZEN=true` with `uv run --offline`
to use the pre-existing locked environment without resolving the unavailable extra.
The view model and renderer tests do not require Streamlit. Ten real Streamlit AppTest
cases (five pages × real/synthetic DB) are collected but skip until the extra is installed.
Their skipped status is a validation limitation, not evidence of browser/UI acceptance.
