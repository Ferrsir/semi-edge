# `trend_v1` results

Spec: `docs/specifications/trend_v1.md`. Spec, null and gates committed at `9c3d307` before any
run. Rules and engine unchanged from `swing_v1` (`f0aa495`). Data: Alpaca SIP split-adjusted
daily cache, 2016-01-04..2026-11-13. Labels: survivorship-biased universes, no earnings
blackout, price returns only, daily-bar stop fills, 2016-2026 was mostly a rising market.

Commands, one run each:

```
semiedge backtest-trend --sample holdout --equity 5000 --risk-profile 5k
semiedge backtest-trend --sample unseen --equity 5000 --risk-profile 5k
semiedge backtest-trend --sample unseen-growth --equity 5000 --risk-profile 5k
```

All figures at 10 bps a side. "Edge" is the rule's mean R minus the random-entry null Tn, with
its 95% bootstrap interval. The $5,000 run uses the reset halt model.

## T1: weekly rotation into the three highest-momentum stocks

| sample | trades | mean R | edge over Tn | $5,000 net | CAGR | max DD | MC3 positive | gates |
|---|---|---|---|---|---|---|---|---|
| U80 holdout, 2025-2026 | 58 | +0.903 [+0.250, +1.715] | +0.529 [-0.128, +1.335] | +13.9% | +7.5% | -5.0% | 93.4% | met |
| X60 non-tech, 2016-2026 | 335 | +0.103 [-0.045, +0.268] | -0.111 [-0.266, +0.060] | +33.8% | +2.8% | -10.4% | 90.2% | **failed** |
| Y60 growth, 2016-2026 | 318 | +0.519 [+0.283, +0.825] | +0.301 [+0.056, +0.614] | +88.3% | +6.2% | -13.9% | 99.2% | met |

X60 misses three gates: mean R without the five best trades is -0.006, the MC1 interval
includes zero, and the rule does not beat the null.

## T2: 100-day closing high

| sample | trades | mean R | edge over Tn | $5,000 net | CAGR | max DD | MC3 positive | gates |
|---|---|---|---|---|---|---|---|---|
| U80 holdout, 2025-2026 | 342 | +0.549 [+0.295, +0.834] | +0.175 [-0.097, +0.489] | +10.2% | +5.5% | -6.3% | 87.6% | met |
| X60 non-tech, 2016-2026 | 1234 | +0.122 [+0.039, +0.211] | -0.092 [-0.187, +0.008] | +14.7% | +1.3% | -18.5% | 75.9% | **failed** |
| Y60 growth, 2016-2026 | 874 | +0.426 [+0.262, +0.609] | +0.207 [+0.034, +0.393] | +55.9% | +4.3% | -16.5% | 96.3% | met |

## Verdict: FAIL

A rule had to meet every gate on all three samples. Both rules fail on X60, so `trend_v1`
fails and no strategy in this project has passed its pre-registered test.

## What the numbers do and do not show

Including the `swing_v1` development run (U80 2016-2024: +100.7%, CAGR +8.3%, maximum drawdown
-11.8%, MC3 99.5%), T1 has now been run on four samples.

- **The account made money on all four**, and at least 90% of the Monte Carlo account paths
  ended positive on each. Three of the four were samples the rule had never seen.
- **On technology and growth stocks it also beat a random entry** (Y60, interval above zero;
  the short holdout points the same way). On steady large caps it did not: there, buying any
  stock on any week with the same trailing exit did better (+0.21R against +0.10R).
- **So what T1 offers is exposure with brakes, not stock-picking skill.** It is invested in
  rising stocks while the market is above its 200-day average and stands aside otherwise. Most
  of its return is the market's. It had about 10% to 29% of the account invested, and it earned
  a fraction of the index: +34% against SPY's +267% on X60, +88% against QQQ's +538% on Y60.
- **The results depend on the owner resetting the drawdown halt.** The 5% halt fired about ten
  times in ten years on X60 and on Y60. With a reset 21 sessions later each time, the results
  are the ones above. If the halt is never reset, the same runs end at +18.8% and +20.7%.
- Trade costs barely matter for these rules: results at 5 and 20 bps are within a few points.
- The lists are today's survivors, and the decade was a bull market. A long-only rule has not
  been tested here in a prolonged bear market beyond 2022.

None of this is evidence of a profitable system (ground rule 7). The remaining honest test is
forward: the rule frozen as it is, on bars that do not exist yet
(`docs/specifications/trend_v1_forward.md`).
