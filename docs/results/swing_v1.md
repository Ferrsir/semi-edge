# `swing_v1` results

Spec: `docs/specifications/swing_v1.md` (`ca2937f`). Engine `f0aa495`, committed before the
run. Data: Alpaca SIP split-adjusted daily cache, 2016-01-04..2026-11-13 (U16 and benchmarks
fetched 2026-11-14, the 64 added U80 symbols and the 60 X60 symbols fetched 2026-11-15). Labels:
survivorship-biased universes, no earnings blackout, price returns only, daily-bar stop fills,
five candidates screened.

## Development: U80, 2016-04-01..2024-12-31

`semiedge backtest-swing --sample development --equity 5000 --risk-profile 5k`

At 10 bps a side. Mean R with the MC1 interval, the MC2 edge over the random-entry baseline,
the $5,000 run with the reset halt model, and the MC3 share of positive account paths.

| candidate | trades | mean R | edge over baseline | $5,000 net | max DD | MC3 positive |
|---|---|---|---|---|---|---|
| R1 RSI2 under 10 (`dip_v1` S1) | 3730 | +0.038 [+0.019, +0.057] | +0.046 [+0.026, +0.065] | -1.6% | -20.2% | 48.0% |
| R2 RSI2 under 5 | 3540 | +0.017 [+0.001, +0.032] | +0.014 [-0.002, +0.029] | +6.7% | -17.3% | 69.5% |
| R3 three down days | 7321 | +0.026 [+0.016, +0.037] | +0.024 [+0.013, +0.034] | +25.1% | -14.4% | 91.7% |
| T1 momentum rotation | 264 | +0.380 [+0.143, +0.641] | -0.031 [-0.282, +0.235] | +100.7% | -11.8% | 99.5% |
| T2 100-day breakout | 1362 | +0.319 [+0.225, +0.410] | -0.092 [-0.208, +0.027] | +75.2% | -10.9% | 98.7% |

QQQ bought and held over the period: +363%.

**Selection (mechanical rule): R3.** It is the only candidate that meets all six gates.

- R1 fails on the account: its trades have an edge, but the $5,000 run ends at -1.6% and only
  48% of the account paths are positive.
- R2 fails MC2 (its edge over a random entry day is not distinguishable from zero) and MC3.
- T1 and T2 make the most money and fail MC2. Their baseline, buying any stock of this list in
  an uptrend with the same trailing exit, makes +0.41R a trade. The rules add nothing to that.
  Their return is the return of holding today's surviving tech winners through 2016-2024.
  T1 also has 264 trades against a minimum of 300.

## Confirmation of R3 (selection recorded at `83377cc` before these runs)

### Holdout: U80, 2025-01-02..2026-10-23

`semiedge backtest-swing --sample holdout --candidates R3 --equity 5000 --risk-profile 5k`

| | 5 bps | 10 bps | 20 bps |
|---|---|---|---|
| study mean R (1,956 trades) | +0.043 | +0.034 [+0.015, +0.054] | +0.018 |
| $5,000 net (reset model) | +12.1% | +10.6% | +6.8% |

Baseline R3b: +0.016R over 22,228 trades. Edge +0.019 [-0.000, +0.039]. The $5,000 run at
10 bps: 207 trades, +$529, compound annual return +5.7%, maximum drawdown -3.8%, average gross
exposure 10.3%, no halt. MC3: 93.8% of account paths positive, 5th to 95th percentile of the
final return -0.8% to +22.3%. QQQ bought and held: +38.1%.

**Holdout gates: all met.**

### Unseen symbols: X60, 2016-04-01..2026-10-23

`semiedge backtest-swing --sample unseen --candidates R3 --equity 5000 --risk-profile 5k`

| | 5 bps | 10 bps | 20 bps |
|---|---|---|---|
| study mean R (6,673 trades) | +0.008 | -0.010 [-0.021, +0.001] | -0.047 |
| $5,000 net (reset model) | +9.2% | -7.3% | -30.1% |

Baseline R3b: -0.024R over 81,744 trades. Edge +0.014 [+0.003, +0.025]. The $5,000 run at
10 bps: 1,026 trades, -$365, maximum drawdown -18.3%, 9 drawdown halts. MC3: 30.3% of account
paths positive. SPY bought and held: +267%.

**Unseen-symbol gates: FAIL** (mean R, MC1, portfolio net, MC3).

## Verdict: `swing_v1` FAIL

R3 passed its selection sample and the time holdout on U80 and failed on 60 non-tech stocks. On
those it still beat a random entry day, but not by enough to pay 10 bps a side.
`docs/specifications/swing_v1_y60.md` tests one explanation on a further unseen list.
