# Specification: `closing_momentum_v1`

Status: **Specified (issue #149); no backtest run.** This is a same-day, long-only test of whether
the first half-hour's direction predicts the last half-hour in the 16-stock semiconductor
universe. Every rule, period, threshold and gate below is fixed before any `closing_momentum_v1`
outcome is computed. Any change after results are seen needs a revised spec with rationale before
the new results are read.

## Research rationale (hypothesis, not evidence of an edge)

Gao, Han, Li and Zhou report that the S&P 500 ETF return from the previous close through the first
half-hour predicted its final half-hour return in 1993–2013, with positive out-of-sample predictive
R-squared. They report similar behavior across ten other actively traded ETFs. This is market/ETF
evidence; it does not establish predictability in individual semiconductor stocks, in today's
market, after the costs and risk limits used here. This spec tests that extrapolation and can fail.

Heston, Korajczyk and Sadka report intraday continuation at recurring half-hour slots across
different trading days in individual stocks. That is a different horizon and is not treated as
evidence that a semiconductor's first half-hour predicts its own last half-hour.

Primary references:

- Gao et al., *Intraday Momentum: The First Half-Hour Return Predicts the Last Half-Hour Return*,
  [SSRN paper](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2552752).
- Heston, Korajczyk and Sadka, *Intraday Patterns in the Cross-Section of Stock Returns*,
  [Journal of Finance](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2010.01573.x).

## Data and disclosure

- Alpaca SIP 5-minute bars for the 16 configured tradable symbols and SMH; split-adjusted,
  regular-hours only. The previous session's daily close is used to calculate the first-half-hour
  return.
- A 5-minute bar with zero volume counts as missing. A symbol with any missing bar from 09:30
  through 15:30 gets no signal that session. A missing 15:30 entry bar cancels the entry. Sessions
  with an early close are excluded because they have no 15:30–16:00 window.
- The report labels the current hand-picked universe as survivorship-biased and states that there
  is no point-in-time earnings blackout. Live/paper R16 controls are unchanged.
- The idea was formed after the tests of `intraday_v1`, `intraday_dip_v1` and `gap_reversal_v1`.
  Daily and 5-minute bars through 2026-11-13 were previously inspected for those strategies, and
  2026-10-26..2026-11-13 was inspected for the NVDA gap-reversal idea. That inspected window is
  excluded from both test periods. No first-half-hour-to-last-half-hour statistic for this
  strategy has been computed.

## Periods (fixed now)

- **Development:** 2024-12-16 to 2025-12-31. Selection uses this period only.
- **Holdout:** 2026-01-02 to 2026-10-23. Run once with the selected variant, after the selection
  and its numbers are recorded in a commit.
- **Seen window:** 2026-10-26 to 2026-11-13. Never used for selection or the pass test.

## Notation and timing

- A full session has 78 bars; `k=0` is 09:30–09:35. The first-half-hour bar is `k=5`, which ends
  at 10:00.
- `PC`: previous session's daily close.
- `C5`: close of bar `k=5`.
- `r_stock = C5_stock / PC_stock - 1` and `r_smh = C5_smh / PC_smh - 1`. Both include the
  overnight move and the first 30 minutes, matching the published predictor definition.
- `k=71` is the 15:25–15:30 bar. Signals are decided at its close. Entries fill at the next bar's
  15:30 open plus slippage; positions are sold at the 15:55 open unless the stop or target fires
  first.

## Signal and variants

At the close of `k=71`, one trade at most per symbol and session is eligible. All variants require:

1. A full 78-bar regular session, with every own-symbol bar from `k=0` through `k=71` present.
2. `C5 > PC`, so the stock's previous-close-to-10:00 return is positive.
3. The signal-bar low is below its close and the planned risk is large enough: `R0 = C71 - L71`
   and `R0 >= 0.0005 * C71`.

Two variants are pre-registered:

- **M1, positive market direction:** the shared stock conditions hold and `r_smh > 0`.
- **M2, sector-relative strength:** the shared conditions hold, `r_smh > 0`, and
  `r_stock > r_smh`.

The **baseline** is every symbol-session meeting the shared data and risk conditions, with no
return-direction filter. It asks whether the direction/relative-strength conditions add value over
the same late-session trade.

At the signal, stop and target are fixed: `stop = L71`, `R0 = C71 - stop`, and
`target = C71 + R0` (1R planned reward). A trade is skipped if the next bar's raw open is at or
through the stop or target. The first-half-hour return and signal-bar low are the only price
inputs; no later bar is used to decide eligibility.

## Entry, exit, ranking and sizing

- **Entry:** market buy at the 15:30 open of `k=72`, plus slippage. If that bar is missing or the
  raw open is at or through the fixed stop/target, cancel the entry.
- **Exits:** after the entry bar, an opening gap through stop or target exits at the open; otherwise
  the stop is checked before the target, and the stop wins a same-bar tie. Any remaining position
  is sold at the 15:55 open of `k=77`. Slippage applies on every fill.
- **Ranking:** when eligible signals exceed free slots, M1 ranks by largest `r_stock`; M2 ranks by
  largest `r_stock - r_smh`; the baseline ranks by largest `r_stock`. Ties go alphabetically.
- **Sizing and accounts:** the `5k` profile, $5,000 starting equity, at most three positions, the
  existing 1% daily-loss and 5% drawdown halts, and the `unrestricted`, `margin_pdt` and `cash`
  models. For sizing and R, use the planned signal risk `R0`, as in the shared same-day dip replay.
  The margin PDT model is under $25,000 and refuses a fourth completed entry/exit day trade within
  five sessions. Cash sale proceeds settle the next session.
- **Costs:** 10, 20 and 30 bps slippage on each side; no commission.

No broker orders are placed or simulated against a broker.

## Runs and gates

1. **Signal study:** each eligible symbol-session is an independent one-share trade, with no
   portfolio limits, sizing, halts or account rules. R is `(net exit - entry fill) / R0`. Report
   mean, median, mean without the top five trades, win rate and exits. The 95% interval for mean R
   uses a session-cluster bootstrap: resample session dates with replacement and retain all trades
   from each sampled date together, so correlated semiconductor names are not treated as
   independent sessions. The interval is descriptive, not a gate.
2. **Portfolio runs:** $5,000 across the three account models at all three slippage levels. Report
   end equity, net return, drawdown, stop/target/flatten counts and refusals.

**Selection (development, 20 bps):** carry forward the variant with the higher mean R among those
with:

- at least 300 signal-study trades;
- mean R above 0;
- mean R above the baseline's;
- mean R without the top five trades above 0.

If neither qualifies, the strategy fails and the holdout is not run.

**Pass test (holdout, one run, 20 bps):** the selected variant needs at least 100 trades, mean R
above 0, above the baseline, and above 0 without its top five trades; the $5,000 unrestricted
portfolio run must also finish positive. Otherwise the test fails.

## Implementation order

The spec must merge before the engine is implemented or any result is computed. The engine is
committed before reading real-data outcomes. Run development; record selection in a commit; run
holdout only if a variant qualifies. If development fails, report failure and leave the holdout
unused. No threshold, period, stop, target, filter or gate may be changed after seeing results.

