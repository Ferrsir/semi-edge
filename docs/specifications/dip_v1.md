# Specification: `dip_v1`

Status: **Specified (revision 1.1), being implemented (issue #133).** It is the candidate to replace `intraday_v1`,
which showed no edge on real data (PR #130). The owner delegated the choice of strategy
(2026-11-14, ADR-012). Every rule, number, test period and pass test below is fixed before any
`dip_v1` result has been computed (ground rule 3). Changing one after results are seen needs a new
version of this spec, with its rationale, written before the new results are read.

Long-only swing trading on daily bars: buy a semiconductor stock after a sharp short-term drop
while it and the sector are still in an uptrend, and sell when it bounces back to its short-term
average, after at most three sessions, or at a wide stop.

## Hypothesis (to be tested, not assumed)

Large, liquid stocks that fall sharply over a few days for stock-specific reasons tend to recover
part of the fall within days (short-term reversal), as long as the longer trend is intact. A
fall that the whole sector shares is more often news that keeps going. So a dip is more likely to
bounce when the stock and SMH are above their 50-day averages and the stock has fallen much more
than SMH. **There is no evidence for this in SEMI-EDGE's data yet.** The tests below exist to
try to reject it, including a baseline that asks whether the dip condition adds anything over
buying any day in an uptrend.

Why swing and not intraday (decided before any test): the intraday_v1 run showed that the
slippage on tight intraday stops costs 0.3–0.9R per trade. A multi-day bounce is a larger move
for the same cost. A position held overnight is not a day trade, so the pattern-day-trader rule
rarely binds at $5,000. And daily bars give about ten years of history instead of two. An
intraday dip variant is deferred and was not tested.

## Data

- Alpaca SIP daily bars, split-adjusted (`config/app.yaml`), the 16 tradable symbols and SMH.
- History is fetched from 2016-01-04, or from the first bar Alpaca returns for a symbol if later.
  A symbol is eligible once it has 60 completed sessions.
- Dividends are not included (label: price returns only; at most a few dividends fall inside a
  three-session hold).
- **A placeholder bar is a missing bar** (revision 1.1): a session whose high equals its low
  and whose volume is under 1,000 shares did not trade normally (SMCI's 2018–2020 delisting,
  VRT's pre-merger SPAC shares) and is treated exactly like a missing bar.

## Periods (fixed now)

- **Development:** 2016-04-01 to 2024-12-31. Variant selection uses this period only.
- **Holdout:** 2025-01-02 to 2026-11-13. Run once, with the selected variant, after selection is
  recorded in a commit. Disclosure: prices in this period were used by earlier strategies
  (`momentum_v1`, `intraday_v1`), but no dip statistic of any kind has been computed on it.

## Notation (daily session t, values known at its close)

- `C, O, H, L`: close, open, high and low.
- `SMA_n(t)`: mean of the last n closes, including t (`semiedge.indicators.sma`).
- `ATR14(t)`: Wilder ATR over 14 sessions (`semiedge.indicators.atr`).
- `RSI2(t)`: Wilder RSI over 2 sessions. With `d = C(t) - C(t-1)`, `gain = max(d, 0)` and
  `loss = max(-d, 0)`, the averages start as the simple mean of the first two values and then
  follow `avg(t) = (avg(t-1) + x(t)) / 2`. `RSI2 = 100 - 100 / (1 + avg_gain / avg_loss)`, and
  `RSI2 = 100` when `avg_loss = 0`.
- `r3(t) = C(t) / C(t-3) - 1`, for the stock and for SMH.
- `D(t) = (r3_stock(t) - r3_SMH(t)) / (ATR14(t) / C(t))`: the relative three-day drop, measured
  in the stock's own daily ranges.

## Signal (at the close of session t)

Shared conditions, all required:
1. Trend: `C(t) > SMA50(t)` for the stock, and `C(t) > SMA50(t)` for SMH.
2. The symbol is eligible (60 sessions), has every bar from t-50 to t, and is not already held or
   pending.

Two pre-registered variants; exactly one is carried forward (see "Selection"):
- **S1, RSI dip:** `RSI2(t) < 10`.
- **S2, relative dip:** `D(t) <= -1.5`.

Stop, fixed at the signal: `stop = C(t) - 2 * ATR14(t)`.

## Entries and exits

- **Entry:** market buy at the open of t+1, plus slippage. It is cancelled when the raw open is at
  or below the stop (as in ADR-008), or when `fill - stop < 0.0005 * fill`.
- The entry session is held session 1. On every held session d, in this order:
  1. **Stop:** on sessions after the entry session, if `O(d) <= stop` the position is sold at
     `O(d)`. Otherwise, on any held session including the entry session, if `L(d) <= stop`, it is
     sold at the stop. Slippage applies in both cases.
  2. **Bounce exit:** if `C(d) > SMA5(d)`, sell at the open of d+1.
  3. **Time exit:** if session 3 closes without an exit, sell at the open of the next session.
- A symbol can signal again from the session after its exit.

## Ranking when signals exceed free slots

S1: lowest `RSI2`. S2: lowest `D`. Ties go to the alphabetically first symbol.

## Sizing and limits

The `5k` risk profile (`config/risk_5k.yaml`, ADR-011), with starting equity of $5,000:
- `shares = min(risk budget / (C(t) - stop), position cap / C(t), gross cap, cash)`, using
  equity at the close of t;
- at the fill, shares are cut to the position cap and to the cash available at the fill price;
- at most 3 positions.

Halts in the portfolio runs: after a session whose close equity is 1% or more below the previous
close, no new entries are made the next session. After a 5% fall from the highest close equity,
no new entries are made for the rest of the run (in production this halt waits for an owner
reset).

## Account models (as in `intraday_v1`, adapted to overnight holds)

- `unrestricted`.
- `margin_pdt`: equity under $25,000. A day trade is an exit in the entry session, which only the
  stop can cause. If that would be a fourth day trade within five sessions, the stop exit is not
  made that session and the position is sold at the next open. The report counts these cases.
- `cash`: sale proceeds settle the next session, and a buy needs settled cash.

## Costs

Slippage of 10, 20 and 30 bps on each side, with no commission. There is no financing cost,
because gross exposure stays at or below 75% of equity.

## Two kinds of runs

1. **Signal study (the edge test).** Every signal is taken as its own one-share trade, with no
   positions limit, sizing, halts or account rules, and is scored in R
   (revision 1.1: `(exit - fill) / (C(t) - stop)`, where `C(t) - stop = 2 * ATR14(t)` is the risk
   planned at the signal; the portfolio runs report R the same way). It isolates whether the
   signal has an edge.
2. **Portfolio runs.** The $5,000 account through all three account models, at all three
   slippages, with the limits above. They show what the account would have experienced.

**Baseline** for the signal study: every symbol-session that meets the shared conditions only
(trend and eligibility, with no dip condition), with the same entry, stop and exits.

## Selection (development period only)

At 20 bps, signal study: carry forward the variant with the higher mean R among those with:
- at least 200 trades;
- a mean R above 0;
- a mean R above the baseline's mean R.

If neither variant qualifies, `dip_v1` fails, the holdout is not run, and the result is reported
as a failure. The selection and its numbers are recorded in a commit before the holdout run.

## Pass test (holdout, one run)

The selected variant passes only if, at 20 bps:
1. the signal study has at least 60 trades, with a mean R above 0 and above the baseline's;
2. the $5,000 portfolio run (unrestricted) ends with a positive net result.

Also reported, without being gates: the 95% bootstrap interval of mean R, results at 10 and
30 bps, every account model, maximum drawdown, net result without the best five sessions, and
buying and holding SMH over the same period.

## Labels on every report

- Survivorship-biased universe (today's hand-picked list, with no delisted names).
- Earnings blackout not applied (no point-in-time earnings history).
- Price returns only; dividends excluded.
- Daily bars: an intraday stop fill assumes the stop price was reachable at the level shown.

## Live and paper use (later tasks, not in this spec's engine)

An entry is refused unless verified earnings data shows no earnings release from the entry session
through session 4 (R16 and `earnings_blackout_sessions` stay unchanged). Orders are placed through
the paper client only.

## Revision 1.1 (2026-11-14, before any valid result)

The first development run (engine `76d6ed5`) is void. Its mean R came from three one-share
trades in SMCI scoring +363R to +440R. SMCI's feed has 349 frozen bars (high = low, zero volume)
for its 2018–2020 delisting. The zero ATR put the stop at the price, and the jump when trading
resumed divided by that near-zero risk. Without its top five trades, every variant and the
baseline had a negative mean R. Two measurement fixes, with no change to any signal, threshold,
exit, sizing rule, period or gate:

1. Placeholder bars count as missing (see "Data").
2. R is measured against the risk planned at the signal, `2 * ATR14(t)`, not `fill - stop`.
   With the old definition, an open that gapped to just above the stop could still turn a small
   bounce into tens of R and dominate the mean.

The development run is repeated once under revision 1.1, and the selection is taken from that
run. The void run's numbers are kept in the record (PR #135); they selected S1, a result that is
discarded.

## Implementation plan (separate PRs)

1. Daily-history fetch from 2016-01-04 (the existing `data fetch`).
2. `backtest/dip.py` with the signal study, the portfolio replay and the CLI, committed before any
   real-data run.
3. Development run, then a selection commit, then one holdout run.
