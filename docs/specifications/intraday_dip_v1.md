# Specification: `intraday_dip_v1`

Status: **Specified, not implemented (issue #137).** It is SEMI-EDGE's main strategy (owner
decision 2026-11-14, ADR-013): same-day dip-buying, many trades a day, nothing held overnight.
Every rule, number, period and pass test below is fixed before any `intraday_dip_v1` result
has been computed (ground rule 3). Changing one after results are seen needs a new revision of
this spec, with its rationale, written before the new results are read.

Long-only on 5-minute bars. Buy a semiconductor stock when it is stretched well below today's
VWAP and prints a reversal bar. Sell at the VWAP it was stretched from, at a stop under the
day's low, or at 15:55. No daily trend filter is used (owner decision).

## Hypothesis (to be tested, not assumed)

Within a session, a liquid large-cap stock that has been pushed far below its volume-weighted
average price tends to snap part of the way back once selling pauses. That happens often enough,
and far enough, for a target at VWAP to beat a stop under the day's low plus trading costs. A
stretch that is larger than the sector's own move (the stock fell more than SMH since the open)
is more likely to be an overshoot than news. **There is no evidence for this yet.** The baseline
below asks whether the stretch adds anything over buying any reversal bar under VWAP.

## Data

- Alpaca SIP 5-minute bars, split-adjusted, regular hours (the #118 cache), for the 16 tradable
  symbols and SMH; daily bars for the prior session's ATR.
- A session with a missing 5-minute bar for a symbol allows no decision for that symbol from the
  gap on (as in `intraday_v1`). A bar with zero volume counts as missing.

## Periods (fixed now)

- **Development:** 2024-12-16 to 2025-12-31. Variant selection uses this period only.
- **Holdout:** 2026-01-02 to 2026-11-13. Run once, with the selected variant, after the selection
  is recorded in a commit. Disclosure: `intraday_v1` (breakouts) was run on 2024-12-16..2026-11-13
  and the last two weeks of daily SMH and stock closes were looked at for `dip_v1`; no
  statistic of any intraday dip, stretch or VWAP-reversion rule has been computed on any period.

## Notation (session s, bars indexed k = 0 for the 09:30 bar)

- `O, H, L, C, V`: the bar's open, high, low, close and volume.
- `VWAP_k`: cumulative, using the typical price `(H + L + C) / 3` (as in `intraday_v1`).
- `ATR_d1`: Wilder ATR(14) of daily bars up to the previous session (needs 15 daily bars).
- `LOD_k`: the lowest low of bars 0..k.
- `stretch_k = (VWAP_k - C_k) / ATR_d1`: how far below VWAP the close is, in daily ranges.
- `rel_k = (C_k / O_0 - 1) - (SMH C_k / SMH O_0 - 1)`, in units of `ATR_d1 / O_0`: the stock's
  move since the open minus SMH's.
- `n`: bars in the session (78 on a full day, 42 on a 13:00 close).

## Signal (decided at the close of bar k)

Decision bars are `k = 3 .. min(71, n - 7)` (09:45 to 15:30 on a full day), with every bar of the
symbol present from 0 to k.

Shared conditions, all required:
1. **Reversal bar:** `C_k > O_k` and `C_k > H_{k-1}`.
2. **Still below VWAP:** `C_k < VWAP_k`.
3. **Room to the target:** `VWAP_k - C_k >= C_k - LOD_k` (planned reward at least the planned
   risk).
4. The symbol has no open or pending position.

Two pre-registered stretch conditions; exactly one is carried forward (see "Selection"):
- **V1, VWAP stretch:** `stretch_{k-1} >= 0.5`.
- **V2, relative stretch:** `rel_{k-1} <= -0.5`.

**Baseline:** the shared conditions only.

At the signal: `stop = LOD_k`, `target = VWAP_k` (fixed prices), and planned risk
`R0 = C_k - LOD_k`. A signal with `R0 < 0.0005 * C_k` is skipped.

## Entries and exits (same session only)

- **Entry:** market buy at the open of bar k+1, plus slippage. It is cancelled if the raw open is at
  or below the stop, or at or above the target.
- **Exits,** checked on every bar from the entry bar on, in this order:
  1. **Gap:** after the entry bar, if `O <= stop`, sell at O; if `O >= target`, sell at O.
  2. **Stop:** if `L <= stop`, sell at the stop. A bar that touches both levels counts as the stop.
  3. **Target:** if `H >= target`, sell at the target.
  4. **Flatten:** an open position is sold at the open of the session's last bar (15:55 on a full
     day).
- Slippage applies to every exit. A symbol may trade again after its exit, any number of times a
  day.

## Ranking when signals exceed free slots

V1: largest `stretch_{k-1}`. V2: lowest `rel_{k-1}`. Baseline: largest `stretch_{k-1}`. Ties go to
the alphabetically first symbol.

## Sizing, limits and accounts

- Profile `5k` (ADR-011), starting equity $5,000: shares are the minimum of the risk budget over
  `R0`, the position cap over `C_k`, the gross cap and the available cash. At the fill, shares
  are cut to the cap and to the cash.
- At most 3 positions.
- **Halts:** after a 1% loss from the session's starting equity, no new entries for the rest of
  the session. After a 5% fall from the highest session-close equity, no new entries for the rest
  of the run.
- **Account models, as in `intraday_v1`:**
  - `unrestricted`;
  - `margin_pdt`: under $25,000, a fourth day trade within five sessions is refused, so every
    entry counts;
  - `cash`: sale proceeds settle the next session.

## Costs

Slippage of 10, 20 and 30 bps on each side, with no commission.

## Two kinds of runs

1. **Signal study (the edge test).** Every signal is taken as its own one-share trade, with no
   positions limit, sizing, halts or account rules. It is scored in planned R:
   `(exit_net - fill) / R0`. Each report also gives the median and the mean without the top five
   trades, so a few outliers can't carry the result.
2. **Portfolio runs.** The $5,000 account through the three account models, at the three
   slippages.

## Selection (development only)

At 20 bps, signal study: carry forward the variant with the higher mean R among those with:
- at least 300 trades;
- a mean R above 0;
- a mean R above the baseline's;
- a mean R without its top five trades that is also above 0.

If neither variant qualifies, the strategy fails, the holdout is not run, and the result is
reported as a failure.

## Pass test (holdout, one run)

The selected variant passes only if, at 20 bps:
1. the signal study has at least 100 trades, with a mean R above 0, above the baseline's, and
   above 0 without its top five trades;
2. the $5,000 unrestricted portfolio run ends with a positive net result.

Also reported: the cash and margin_pdt runs, 10 and 30 bps, maximum drawdown and SMH over the
period.

## Labels on every report

Survivorship-biased universe; no earnings blackout (no point-in-time earnings history); 5-minute
SIP bars, with fills at the next bar's open plus slippage.

## Live and paper use (later tasks)

R16 earnings blackout applies. Orders are placed through the paper client only. Entries are
market orders at the next bar's open; the target is a limit sell and the stop a stop order,
placed together.

## Implementation plan

1. `backtest/intraday_dip.py` with the signal study, portfolio replay and CLI, committed before
   any real-data run.
2. Development run, then a selection commit, then one holdout run.
