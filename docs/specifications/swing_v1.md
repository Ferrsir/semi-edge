# Specification: `swing_v1`

Status: **Specified, being implemented (issue #180).** Owner request of 2026-11-15, after
`skill_follow_v1` failed: find a strategy at any holding period that produces good results, can
be run through a Monte Carlo, and reproduces instead of having got lucky once (ADR-018). Every
rule, number, universe, period, Monte Carlo and gate below is fixed before any `swing_v1`
result has been computed (ground rule 3). Changing one after results are seen needs a new
version of this spec, with its rationale, written before the new results are read.

## Why these candidates

Seven same-day and next-day ideas have failed here. Their moves were small against the cost of
trading them. Two effects are documented over decades in liquid stocks and move further per
trade:

- **Short-term reversal in an uptrend.** A stock that drops sharply for a few days while its
  longer trend is intact tends to recover part of the drop. This is the only effect that beat
  its baseline in every sample tested in this project (`dip_v1`, four samples).
- **Trend and momentum.** Stocks that have risen for months tend to keep rising for a while.
  Individual trades are few and noisy, but winners are allowed to run.

Five candidates are pre-registered: three dip rules and two trend rules. The dip rules follow
published forms (Connors and Alvarez's RSI(2) rules and the three-down-days rule) and the trend
rules follow the standard 6-month momentum and breakout forms. None was tuned on this data.

## Data

- Alpaca SIP daily bars, split-adjusted, 2016-01-04..2026-11-13, price returns only.
- A bar with high equal to low and volume under 1,000 is missing.
- A symbol is eligible at the close of `t` once it has 260 bars through `t`, and every indicator
  the candidate reads is defined at `t`.
- A symbol for which Alpaca returns no bars is dropped and named in the results.

## Universes (fixed now)

- **U80:** as in `skill_follow_v1`. Regime index: QQQ.
- **X60:** 60 large US stocks outside technology, written from memory before any of their bars
  was fetched. No test in this project has read any of them. Regime index: SPY.

```
JPM BAC GS MS WFC C V MA AXP BLK SCHW UNH JNJ LLY PFE MRK ABBV TMO ABT AMGN XOM CVX COP SLB
CAT DE BA GE HON LMT RTX UPS FDX WMT COST HD LOW TGT MCD SBUX NKE DIS CMCSA T VZ KO PEP PG PM
MO LIN NEE DUK SO AMT PLD SPG BKNG GM F
```

Both lists are today's names, so both are survivorship-biased. Long-only results on them are
flattered, the trend rules most of all. Each candidate is therefore also judged against a
baseline drawn from the same list, which shares the bias.

## Periods (fixed now)

| sample | universe | period | role |
|---|---|---|---|
| Development | U80 | 2016-04-01..2024-12-31 | selection |
| Holdout | U80 | 2025-01-02..2026-10-23 | confirmation, one run |
| Unseen symbols | X60 | 2016-04-01..2026-10-23 | confirmation, one run |

2026-10-26..2026-11-13 is in no period (inspected for `gap_reversal_v1`). Disclosure: `dip_v1`
S1 statistics are already known on U80 in both periods (`docs/results/dip_v1_replication.md`),
so candidate R1 is not blind on U80. It is blind on X60. No statistic of R2, R3, T1 or T2 has
been computed on any sample.

## Notation (daily session t, values known at its close)

`O, H, L, C`. `SMA_n`: mean of the last n closes. `ATR14`: Wilder
(`semiedge.indicators.atr`). `RSI2`: as defined in `dip_v1.md`. `MOM(t) = C(t-21) / C(t-147) - 1`
(six months, skipping the latest month). `idx`: the universe's regime index.

## Candidates

All are long-only. A signal at the close of `t` is filled at the open of `t+1` plus the cost.
The entry is cancelled when the raw open is at or below the stop, or when
`fill - stop < 0.0005 * fill`. `risk = C(t) - stop`, fixed at the signal.

### Dip rules

| | R1 (`dip_v1` S1) | R2 (RSI(2) under 5) | R3 (three down days) |
|---|---|---|---|
| Trend filter | `C > SMA50` | `C > SMA200` | `C > SMA200` |
| Regime | `idx C > idx SMA50` | `idx C > idx SMA200` | `idx C > idx SMA200` |
| Dip | `RSI2 < 10` | `RSI2 < 5` | `C(t) < C(t-1) < C(t-2) < C(t-3)` and `C < SMA5` |
| Stop | `C - 2 * ATR14` | `C - 3 * ATR14` | `C - 3 * ATR14` |
| Bounce exit | `C > SMA5`, sell next open | same | same |
| Time exit | after 3 held sessions | after 7 held sessions | after 7 held sessions |
| Rank | lowest `RSI2` | lowest `RSI2` | lowest `C / SMA5` |

Baselines **R1b, R2b, R3b**: every eligible symbol-session that meets the trend filter and the
regime, without the dip, with the same stop and exits. A random entry day has this expectation.

### Trend rules

Shared: regime `idx C > idx SMA200`, trend filter `C > SMA50`, initial `stop = C - 3 * ATR14`.
Exits: the initial stop intraday; a trailing exit when
`C(d) < highest close since entry - 3 * ATR14(d)`, sold at the next open; a time exit after 120
held sessions. Rank: highest `MOM`.

- **T1 momentum rotation:** on the last session of each week, the three eligible symbols with
  the highest `MOM` among those that pass the trend filter.
- **T2 breakout:** `C(t)` above each of the 100 closes before it.
- **Tb baseline:** on the last session of each week, every eligible symbol that passes the
  trend filter and the regime.

### Held-session rules (all candidates)

On every held session `d`, in this order:
1. An exit decided at the previous close is sold at `O(d)`.
2. After the entry session, if `O(d) <= stop` the position is sold at `O(d)`.
3. If `L(d) <= stop` the position is sold at the stop.
4. At the close: the bounce or trailing condition, then the time exit, set an exit for the next
   open.

A missing bar defers the step to the next session with a bar.

## Signal study (the edge test)

One-share trades scored in R: `R = (exit * (1 - cost) - fill) / risk`. Dip rules and their
baselines take every signal as its own trade, as `dip_v1` does. Trend rules and Tb take a
symbol's next signal only after its previous study trade has closed, because their trades last
months and would otherwise overlap themselves. A trade still open at the end of the period is
closed at the last close. Costs: **5, 10 and 20 bps a side, decided at 10 bps** (as in
`orb_inplay_v1`, "Costs").

## $5,000 portfolio run

The `5k` profile unchanged (`config/risk_5k.yaml`): 1% of equity at risk per trade, 25% position
cap, 75% gross cap, 3 positions, the #154 actual-fill risk cap. Signals at a close are ranked
and taken while slots are free. A symbol held or pending does not signal. Sizing uses equity at
the signal close. One account model (unrestricted): these are multi-day holds, so the
pattern-day-trader rule does not bind.

Halts:
- After a session that closes 1% or more below the previous close, no entries the next session.
- After a 5% fall from the highest close equity, no new entries. Open positions run their exits.
  Two models of the owner reset this halt waits for. **Reset** (used for the gates): entries
  resume 21 sessions after the halt, and the drawdown is measured from equity at that point.
  **Strict** (reported): entries never resume. The limit itself is unchanged in both.

## Monte Carlo (fixed now, seed 0)

- **MC1, trade bootstrap:** 2,000 resamples of the study's trades give the 95% interval of
  mean R.
- **MC2, edge over random entry:** 2,000 resamples of (candidate mean R minus baseline mean R),
  resampling each side's trades independently. The lower 2.5% bound must be above 0.
- **MC3, account paths:** 5,000 paths built from the portfolio run's daily returns by a block
  bootstrap (blocks of 21 sessions, circular), each as long as the run. Reported: the share of
  paths that end above the start, the 5th, 50th and 95th percentile of the final equity, and
  the median and 95th percentile of the maximum drawdown.

## Selection (development, U80, 10 bps)

A candidate qualifies only with all of:

1. at least 300 study trades;
2. mean R above 0, and mean R without the five best trades above 0;
3. MC1 lower bound above 0;
4. MC2 lower bound above 0;
5. portfolio run (reset model) with a positive net result;
6. MC3: at least 90% of paths end above the start.

At most **two** candidates go to confirmation: the qualifiers with the highest portfolio net
result, ties to the alphabetically first. If none qualifies, `swing_v1` fails and no
confirmation run is made. The selection is recorded in a commit before any confirmation run.

## Confirmation (one run per selected candidate, 10 bps)

- **Holdout, U80 2025-2026:** at least 40 study trades, mean R above 0 and above the
  baseline's, mean R without the five best trades above 0, positive portfolio net (reset).
- **Unseen symbols, X60 2016-2026:** at least 300 study trades, mean R above 0, MC1 lower
  bound above 0, MC2 lower bound above 0, positive portfolio net (reset), MC3 at least 90%.

A candidate is a **PASS** only if both hold.

## Reported for every run, not gates

Results at 5 and 20 bps, compound annual return, maximum drawdown, average gross exposure, the
strict-halt run, the number of halts, and the regime index bought and held over the same period.
The index comparison matters: a long-only rule in a rising market can pass every gate here and
still earn less than holding the index.

## Labels on every report

Survivorship-biased universes. Earnings blackout not applied. Price returns only. Daily bars:
a stop fill assumes the stop price traded. Five candidates were screened.

## Live, SIM and paper use

Nothing here authorizes SIM or paper orders. A PASS makes the candidate the strategy that the
SIM and paper plumbing (#21, #162) is built to run, with R16 and every risk limit unchanged.
