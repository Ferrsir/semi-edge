# Specification: `trend_v1`

Status: **Specified (issue #180), before any result on its samples.** Every rule, sample and
gate is fixed here before any trend statistic has been computed on the U80 holdout, on X60 or
on Y60 (ground rule 3).

## Why this test exists, and what it does not undo

`swing_v1` screened two trend rules on U80 in 2016-2024. Both made money ($5,000 became about
$10,000 with T1 and $8,800 with T2) and both failed, because neither beat its baseline Tb.
That verdict stands: **ranking by momentum, or waiting for a 100-day high, adds nothing over
buying any stock that is in an uptrend.**

But Tb is not a random entry. It has the same uptrend filter, market-regime filter and trailing
exit as T1 and T2, so it is a trend-following rule itself, and it made +0.41R a trade. The
`swing_v1` gate therefore never asked the question that matters for the owner's request: does
long-only trend following, run under the `5k` risk profile, make money on stocks and periods
it has not seen, and is that more than what a random entry with the same exits makes?

The dip rules are closed: R3 failed on X60 and on Y60 (`docs/results/swing_v1.md`).

## Disclosure

- The U80 2016-2024 results of T1, T2 and Tb are known and are the reason for this spec, so
  that sample is not a test here.
- This spec changes the null that T1 and T2 are compared with, after they failed against the
  old one. The reason is stated above, and the new comparison is made only on samples no trend
  rule has read: the U80 holdout, X60 and Y60. Those samples were read for dip statistics only.
- Two rules on three samples. A rule must pass all three.

## Rules (unchanged from `swing_v1.md`)

T1 (weekly rotation into the three highest six-month momentum stocks) and T2 (100-day closing
high), with the shared regime filter, trend filter, 3-ATR initial stop, 3-ATR trailing exit and
120-session time exit, exactly as specified and implemented for `swing_v1` (engine `f0aa495`).
Costs, fills, the signal study and the $5,000 portfolio run are unchanged.

**Null Tn (new):** on the last session of each week, every eligible symbol with a defined
`MOM`, with no trend filter and no regime filter, and the same stop and exits. This is what
entering at an arbitrary time and letting the same exits work would have made.

## Samples (one run each)

| sample | universe | period |
|---|---|---|
| Holdout | U80 | 2025-01-02..2026-10-23 |
| Unseen symbols | X60 | 2016-04-01..2026-10-23 |
| Unseen growth symbols | Y60 | 2016-04-01..2026-10-23 |

## Gates (10 bps a side)

On X60 and on Y60, each separately:
1. at least 150 study trades;
2. mean R above 0, and mean R without the five best trades above 0;
3. MC1 lower bound above 0;
4. MC2 against Tn: lower bound of (rule mean R minus Tn mean R) above 0;
5. positive $5,000 net result (reset halt model);
6. MC3: at least 90% of account paths end above the start.

On the U80 holdout (22 months, too short for the interval gates): at least 30 study trades,
mean R above 0 and above Tn's, and a positive $5,000 net result.

A rule is a **PASS** only if it meets every gate on all three samples.

## Reported, not gates

Tb on each sample. Results at 5 and 20 bps. The strict-halt run and the number of drawdown
halts: the reset model assumes the owner resets the 5% halt after 21 sessions every time, and
the strict run shows what happens if they never do. Compound annual return, maximum drawdown,
average gross exposure, and the index bought and held. A long-only trend rule earns most of its
return from the market rising. With about a fifth of the account invested it will earn far less
than the index in a bull market, and the report must show that next to the result.

## What a result means

- PASS: the rule becomes the candidate for a frozen-rule forward test and for the SIM and paper
  plumbing (#21, #162). It is a rule for being invested in rising stocks while the market rises
  and standing aside otherwise. It is not evidence of stock-picking skill, and not evidence of a
  profitable system until forward results exist (ground rule 7).
- FAIL: no strategy tested in this project has reproduced, and the search stops for an owner
  decision.

## Labels

Survivorship-biased universes. Earnings blackout not applied. Price returns only. Daily-bar
stop fills. 2016-2026 was mostly a rising market.

## Command

`semiedge backtest-trend --sample holdout|unseen|unseen-growth --equity 5000 --risk-profile 5k`
