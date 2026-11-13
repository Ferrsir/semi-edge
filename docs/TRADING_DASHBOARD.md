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

## Layout and formatting

The synthetic banner, when present, is the first thing on every page, as a warning box. Each
page has one heading, with the mode and data source on a line under it. Section names are
subheadings. Overview opens with a stale-heartbeat alert for each stale component (naming
the component, its age and its expected interval), then an "At a glance" table: equity,
change since the previous close, open positions, active halts, and the last run. Next to it
is an equity line for the last sessions, drawn only when at least four snapshots exist. The
equity table stays below it as the readable fallback.

Tables show readable values. Times are `YYYY-MM-DD HH:MM UTC`, money has thousands
separators and two decimals, ranks and quantities are whole numbers, eligibility is yes/no,
and ages read like `45 s` or `22 min`. A missing value shows as `—` and is never filled in.
Signals show close, ATR, stop, target and rejection reasons as columns instead of the raw
payload JSON; an unreadable payload says so in those columns. Formatting lives in
`semiedge.dashboard.format` and runs before `display`, so it never decides what is safe to
show. Widgets that render Markdown (subheaders, captions, alerts) only receive fixed text,
validated enums, numbers, or a component name that matches a strict pattern. Streamlit's
developer toolbar is hidden (`client.toolbarMode = "viewer"`).

Overview, Signals, Positions, Trade history and Operations render persisted records
in plain-text tables. Signals select the newest run that actually contains evaluations,
irrespective of the job kind. Trade history is limited to the newest 500 orders/fills;
operations to 100 runs/reconciliations/errors. Protection uses all persisted protection
orders, showing their actual statuses and levels rather than assuming protection exists.
Held sessions, when equity snapshots exist, are exchange sessions since entry as of the
latest persisted equity session (entry session counts as one, per ADR-005); otherwise unavailable.

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

## Dependencies and validation

`dashboard = ["streamlit>=1.40"]` is an optional dependency and its resolved packages
are included in `uv.lock`. Install with `uv sync --locked --extra dashboard`.

The view-model and fake-renderer tests run without Streamlit. The ten Streamlit
AppTest cases (five pages × real/synthetic DB) require the dashboard extra. Current
CI installs only the base dependencies, so these ten cases skip in CI; run them
locally with the extra installed. Skips do not establish UI acceptance.
The CI dependency audit includes all extras, including Streamlit (PR #87).
