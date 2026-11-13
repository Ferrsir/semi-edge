# Performance reports (T6 / issue #16)

Run `semiedge metrics RESULT.json` against a backtest artifact. The command emits
labels-first JSON, including both the metrics command and the original run
command. It does not fetch market data or access a broker. No performance results
are recorded in this document.

The report contains whole-period metrics, calendar-year and intersecting stress
periods, the deepest drawdown episodes, regime-conditioned returns, and benchmark
comparisons for the whole run and each reported window. Undefined metrics have
`value: null` and a reason. JSON never permits non-finite numbers.

Implementation decisions for gaps in the interface specification:

- The original result schema did not retain pre-session capital or the settings
  needed for comparisons. New artifacts add optional `initial_equity` and
  `metrics_context` fields, without changing the existing schema version. Context
  records the original universe, parameters, costs, calendar, and provenance.
  Historical artifacts lacking context are explicitly rejected by the CLI;
  starting capital is never inferred from the first close or current config.
- The metrics API reads the starting capital from the result. Holding time uses
  the stored `held_sessions`, including the exit day, without subtracting a day.
- Period returns start at their first close, as required by the rebasing rule.
  Ratios are computed on dollar equity because a constant rescaling cancels;
  dollar trade expectancy retains its units. Full completed trades are attributed
  by entry date, even when the exit lies outside the reporting period.
- A drawdown episode runs from a peak to its deepest trough; its duration includes
  recovery, or extends through the last observed session if unrecovered. A peak
  before the first close uses the recorded starting capital. Episodes are ranked
  by depth with chronological tie breaking.
- Regime returns compound only the sessions assigned to each regime, using the
  previous exchange session's close and a complete SMA window. Trades use entry
  session attribution. Missing classification history makes the breakdown
  explicitly unavailable.
- Buy-and-hold buys the maximum affordable whole-share quantity at the first
  open, reserves commissions, and marks at every close, including the final
  close. The position remains held, so only the executed buy contributes costs
  and turnover. Closed-trade metrics are unavailable; R is explicitly undefined
  because a buy-and-hold position has no strategy stop or risk unit.
- The benchmark row type cannot itself be null in the specified API. Missing,
  stale, invalid or mismatched inputs therefore retain the named comparison row
  with undefined metrics and reasons. No prices are forward-filled.
- The CLI reconstructs synthetic inputs or reads the cache using saved settings.
  The original full data fingerprint must match before comparison data is used.
  A missing/changed cache produces an explicit report label and unavailable
  benchmark/regime rows while retaining the strategy metrics. Fixture artifacts
  without reproducible stored provenance likewise retain unavailable comparisons.
- Annualisation uses the specified session constant and Sharpe assumes a zero
  risk-free rate; both assumptions are included in report metadata.

Validation uses hand-built synthetic bars and hand-computed equity/trade fixtures
in `tests/unit/backtest/test_metrics.py`; no downloaded observations or measured
strategy performance are embedded in tests or documentation.
