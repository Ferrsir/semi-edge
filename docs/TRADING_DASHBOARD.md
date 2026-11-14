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
in plain-text tables (Overview also shows the desk; see below). Signals select the newest run that actually contains evaluations,
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
untrusted-text normalizer, except on the desk, which escapes it (see Rendering and escaping).

Operations requires an actor, reason and exact typed `HALT`. Its only mutation calls
`request_manual_halt`, which inserts one idempotent manual halt. This stops new entries;
exits and protective stops continue. There are no order controls or halt removal controls.

## Layout and formatting

Overview is the desk (#108), an all-dark screen modelled on an owner-supplied reference:
- a status header (PAPER plus RUNNING, HALTED or STALE HEARTBEAT) and the latest scan session;
- a hero card with total equity, the change and percentage since the first snapshot, the closed
  trade record, and an equity curve;
- tiles for signals evaluated, filtered out and orders placed;
- one card per pipeline stage: scanner, rules, risk manager, orders and stop guard;
- a signal funnel: filtered by a hard rule, not ordered because the risk check said no, ordered;
- "Where the bot is trading": open positions with entry, stop, target, sessions held, and whether a
  live stop order exists;
- closed trades with realized P&L from exit fills against the average entry price;
- the most common rejection reasons in the latest scan, and the heartbeat strip.

Every figure comes from the state database. When something is missing, the desk says so or shows
an empty state, and never fills in a number. The records tables follow the desk under "Records".
The synthetic banner stays the first thing on every page.

Tables show readable values. Times are `YYYY-MM-DD HH:MM UTC`, money has thousands separators and
two decimals, ranks and quantities are whole numbers, eligibility is yes/no, and ages read like
`45 s` or `22 min`. A missing value shows as `—`. Signals show close, ATR, stop, target and
rejection reasons as columns instead of the raw payload JSON.

## Rendering and escaping

The desk is the only HTML the dashboard renders. The owner approved this exception to the
plain-text rule on 2026-11-13 (#108); every other page still uses plain-text widgets.
`semiedge.dashboard.desk` builds it as one fragment for `st.html`:
- every string from the database or a broker goes through `UntrustedText` cleaning and then
  `html.escape`; symbols must also match the store's symbol pattern, or render as `?`;
- numbers are formatted by the desk itself;
- the stylesheet is a fixed constant;
- the equity curve and icons are SVG built from numbers and fixed paths, embedded as
  `data:image/svg+xml` images (Streamlit's sanitizer drops inline `<svg>`, and an SVG loaded as
  an image cannot run script).

`tests/safety/test_dashboard_desk.py` feeds script tags, event-handler attributes and style
breakouts through every text field and checks that no real tag carries a handler or a non-data
`src`. Widgets that render Markdown (subheaders, captions, alerts) only receive fixed text,
validated enums, numbers, or a component name that matches a strict pattern. Streamlit's
developer toolbar is hidden (`client.toolbarMode = "viewer"`), and the theme in
`.streamlit/config.toml` matches the desk's palette.

## Charts

The Charts page (#116) reads the local price cache read-only (`ParquetBarStore`, the configured
SIP feed and split adjustment) and needs no state database, so it works before the bot has run a
session. It draws daily candles with the 20- and 50-day averages and volume for any cached
symbol, over 3 months, 6 months or a year. Averages are computed on the full cached history and
undefined values are left out, never filled in; a malformed bar is skipped. The chart is a plain
Vega-Lite spec built by `semiedge.dashboard.charts`, so the base install tests it. Choosing
"5-minute" (#121) shows one session's regular-hours 5-minute candles from the intraday cache in
New York time, with the running VWAP, the opening range (09:30-09:45 high and low, only when all
three bars exist) and the prior day's high from the daily cache: the levels `intraday_v1` trades. When the state database is missing, the other
pages keep their "Dashboard unavailable" error and point to the Charts page.

## Auto-refresh

A sidebar switch (on by default) re-runs the open page every 60 seconds with Streamlit's
`st.fragment(run_every=...)`, so new cache data or state rows appear without a click; each run
shows its UTC time. The Operations page is never re-run on a timer, because the manual-halt form
would lose what the operator is typing. **Refresh** still re-runs everything at once.

## Dependencies and validation

`dashboard = ["streamlit>=1.40"]` is an optional dependency and its resolved packages
are included in `uv.lock`. Install with `uv sync --locked --extra dashboard`.

The view-model and fake-renderer tests run without Streamlit. The ten Streamlit
AppTest cases (five pages × real/synthetic DB) require the dashboard extra. Current
CI installs only the base dependencies, so these ten cases skip in CI; run them
locally with the extra installed. Skips do not establish UI acceptance.
The CI dependency audit includes all extras, including Streamlit (PR #87).
