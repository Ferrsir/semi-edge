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
