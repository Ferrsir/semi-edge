# Backtest engine (T5 / #15)

The `semiedge.backtest.engine` API and `semiedge backtest --start D --end D` implement
phase2-4-interfaces.md §3. This is offline research code. It does not create broker clients,
read credentials, contact endpoints, or alter the strategy or risk limits. T6 owns metrics.
The CLI accepts `--source cache|synthetic` and an optional `--output DIR`. Existing output
paths are refused. Without `--output`, artifacts use the configured data directory.

The cost assumptions in `config/backtest.yaml` were fixed during implementation, using only
hand-built test fixtures. They are modelling assumptions, not claims about current broker
fees or execution quality. Per-share commission is zero to avoid adding split-scale dependence;
whole-share rounding still has that limitation. No performance results informed these values.

The engine truncates frames before each quality assessment and strategy evaluation. It uses
calendar sessions for warm-up, every event, and holding periods. The pre-start decision can
produce fills on the first requested session. Entries use the shared proposal builder and a
required gate; the CLI supplies `RiskManager`. Exits bypass the gate. A separate real manager
supplies the shared halt calculation even when a unit test injects a fake entry gate.

Resolved contract gaps and metadata choices:

- A time exit without an executable bar stays pending. Stops/targets are not evaluated for a
  missing bar, and the position keeps its last close mark with an explicit data-gap record.
  A missing final held-symbol bar aborts with `end_of_window_no_bar`: there is no `close_end`
  at which to execute the mandatory liquidation. No partial artifact is written.
- `BacktestData.fixture_manifest` is optional metadata, required for fixture sources so the
  mandated provenance label can be rendered. The present cache schema does not retain this
  manifest; its loader refuses fixture series instead of inventing provenance. Direct fixture
  callers provide the manifest. Non-split adjustment modes are refused because this contract
  specifies price returns excluding dividends.
- T17's corporate-action types do not exist yet. The cache loader accepts `None` and renders
  `CORPORATE ACTIONS NOT CHECKED`; a non-None adapter fails explicitly pending T17 integration.
- The pure request API has no full AppConfig, scanner settings, git context, or creation clock.
  Its configuration fingerprint hashes every configuration field actually supplied in the
  request. The CLI replaces that metadata with `run_fingerprint(app, BacktestSettings)` and
  the local git SHA. The pure API leaves git SHA absent. Neither path reads a new wall clock.
- Run IDs reuse `reporting.new_run_id`, using the final session close as their timestamp and a
  random token for exclusive artifact names. IDs and git SHA are excluded from deterministic
  payload comparison. Metadata does not influence event execution.
- `save(directory)` treats its argument as the artifact root, adding `reports/backtests/` or
  `synthetic/reports/backtests/`. Labels are the first JSON field, and every artifact uses
  exclusive creation. The CLI prints labels before command/path information, without metrics.

Tests use small, hand-computable synthetic OHLCV fixtures. They cover the event ordering,
slipped-fill invalidation, stop-first ambiguity, costs and overflow, pending exits, missing
bars, liquidation, deterministic payloads, provenance, real risk gating, halt latching,
future poisoning/deletion, CLI output exclusivity, and static dependency boundaries.
