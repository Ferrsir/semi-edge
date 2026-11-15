# `skill_follow_v1` results

Spec: `docs/specifications/skill_follow_v1.md` (`ae4ad9d`). Engine `419ea55`, committed before
the run. Every number comes from the command shown, on the Alpaca SIP split-adjusted cache:
U16 and the benchmarks fetched 2026-11-14, the 64 added symbols fetched 2026-11-15 with

```
semiedge data fetch --config-dir config/u80 --symbols <the 64> --start 2016-01-04 --end 2026-11-13
semiedge data fetch-intraday --config-dir config/u80 --symbols <the 64> --start 2024-11-13 --end 2026-11-13
```

All 64 symbols returned bars. Labels: survivorship-biased universes, no earnings blackout, price
returns only, daily-bar candidates assume stop and close fills, 38 tests in one screen.

## Development 2024-12-16..2026-06-30

`semiedge backtest-skill-follow --period development`

Signal study at the decision cost of 10 bps a side. Mean R with its 95% bootstrap interval.
"History" is the same rule on 2016-04-01..2024-12-13 (daily candidates only).

| candidate | U16 trades | U16 mean R | U16 history | U80 trades | U80 mean R | U80 history |
|---|---|---|---|---|---|---|
| B0 baseline, buy at 10:00 | 6079 | -0.064 [-0.086, -0.043] | | 30204 | -0.077 [-0.086, -0.067] | |
| D0 baseline, next open to close | 6144 | -0.024 [-0.039, -0.009] | -0.058 | 30669 | -0.038 [-0.044, -0.031] | -0.060 |
| S0 baseline, three sessions | 6112 | +0.045 [+0.028, +0.063] | +0.015 | 30509 | +0.019 [+0.012, +0.027] | +0.004 |
| N0 baseline, overnight | 5939 | -0.007 [-0.024, +0.009] | | 29154 | -0.020 [-0.027, -0.014] | |
| A0 skill ORB, every trigger | 3629 | -0.228 [-0.266, -0.193] | | 17480 | -0.218 [-0.235, -0.201] | |
| A1 ORB after 20-day breakout | 610 | -0.279 [-0.368, -0.184] | | 2723 | -0.233 [-0.274, -0.189] | |
| A2 ORB after bull flag | 265 | -0.188 [-0.326, -0.037] | | 1264 | -0.280 [-0.344, -0.216] | |
| A3 ORB after 20-day reclaim | 199 | -0.177 [-0.339, -0.020] | | 1001 | -0.112 [-0.183, -0.039] | |
| A4 ORB after oversold | 74 | +0.525 [+0.207, +0.830] | | 633 | +0.009 [-0.088, +0.112] | |
| A5 ORB after oversold bounce | 35 | -0.289 [-0.672, +0.136] | | 297 | -0.193 [-0.315, -0.058] | |
| B1 follow at 10:00, laggards | 1269 | -0.035 [-0.085, +0.011] | | 3572 | -0.045 [-0.073, -0.014] | |
| B2 follow at 10:00, leaders | 1227 | -0.057 [-0.105, -0.008] | | 5014 | -0.064 [-0.089, -0.039] | |
| D1 20-day breakout | 1098 | -0.057 [-0.091, -0.019] | -0.092 | 4978 | -0.044 [-0.061, -0.025] | -0.075 |
| D2 bull flag | 440 | -0.027 [-0.079, +0.030] | -0.072 | 2138 | -0.030 [-0.054, -0.005] | -0.068 |
| D3 20-day reclaim | 361 | -0.035 [-0.095, +0.024] | -0.070 | 1732 | -0.018 [-0.047, +0.011] | -0.062 |
| D4 oversold | 111 | +0.230 [+0.066, +0.401] | +0.011 | 1026 | +0.070 [+0.019, +0.120] | -0.013 |
| D5 oversold bounce | 66 | -0.263 [-0.400, -0.103] | -0.054 | 567 | -0.161 [-0.211, -0.109] | -0.052 |
| D6 daily follow, laggards | 818 | -0.105 [-0.145, -0.063] | -0.084 | 1202 | -0.107 [-0.139, -0.074] | -0.098 |
| D7 daily follow, leaders | 814 | -0.085 [-0.126, -0.043] | -0.106 | 1747 | -0.033 [-0.061, -0.006] | -0.096 |
| S6 three-session follow, laggards | 810 | -0.010 [-0.052, +0.033] | +0.024 | 1161 | +0.060 [+0.025, +0.098] | -0.014 |
| S7 three-session follow, leaders | 806 | -0.025 [-0.066, +0.021] | -0.010 | 1708 | +0.067 [+0.038, +0.095] | -0.025 |
| N1 overnight follow, leaders | 1052 | +0.026 [-0.026, +0.068] | | 3358 | -0.028 [-0.048, -0.009] | |
| N2 overnight follow, laggards | 1044 | +0.017 [-0.010, +0.047] | | 2491 | -0.013 [-0.031, +0.004] | |

**Selection (mechanical rule): none of the 38 pairs qualifies. `skill_follow_v1` fails. The
holdout 2026-07-01..2026-10-23 was not run and stays unread.**

The pairs that came closest, and the gate each missed:

- D4:U16 and A4:U16 (oversold): positive, but 111 and 74 trades against a minimum of 300.
- D4:U80 (oversold, next open to close): passes every development gate, but the same rule
  lost on 2016-2024 (-0.013R over 4,068 trades).
- S6:U80 and S7:U80 (three-session follow): pass every development gate, but both lost on
  2016-2024 (-0.014R and -0.025R). Holding any U80 stock three sessions made +0.019R in the
  development period, so most of their result is the 2025-2026 market.
- N1:U16 and N2:U16 (overnight follow): small positive means whose intervals include zero.

## Reading

- **The skill pack's rules do not carry an edge here.** Its opening-range breakout loses about
  0.22R a trade on both universes at 10 bps and about 0.10R even at 5 bps. Four of its five
  daily setups lose in the development period and in 2016-2024. This agrees with the pack's
  own Time Machine example, which calls the same plan "close to a coin flip" before costs.
- **Sector follow does not pay at these horizons.** After a strong index move, laggards and
  leaders both lose the same day (B1, B2, D6, D7) on both universes and in both periods. The
  stocks do move together, but the shared move is already in the price by the time the rule
  can act, and what is left is smaller than the cost of trading it.
- **The one positive thread is buying oversold stocks.** The oversold setup is the only skill
  rule with a positive mean, on both universes. It is the same idea as `dip_v1` S1, the only
  strategy that has passed a pre-registered holdout in this project. This is an observation
  made after seeing the results, so it is a lead for a new pre-registered test, not a finding.
- Costs decide most of this. At 5 bps a side several candidates sit near zero. At 20 bps
  almost everything is clearly negative.

Full numbers at 5, 10 and 20 bps:
`data/reports/skill_follow/development-419ea55/skill_follow_v1_screen.json` (git-ignored).
